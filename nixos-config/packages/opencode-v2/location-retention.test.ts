import type { FileSystem } from "@opencode/core/filesystem"
import { describe, expect } from "bun:test"
import { Context, Deferred, Duration, Effect, Fiber, Layer, LayerMap, RcMap, Schema } from "effect"
import { TestClock } from "effect/testing"
import { AppNodeBuilder } from "@opencode/core/effect/app-node-builder"
import { LayerNode } from "@opencode/util/effect/layer-node"
import { makeGlobalNode } from "@opencode/util/effect/app-node"
import { Bus } from "@opencode/core/bus"
import { Config } from "@opencode/core/config"
import { Database } from "@opencode/core/database/database"
import { Form } from "@opencode/core/form"
import { Job } from "@opencode/core/job"
import { Location } from "@opencode/core/location"
import { LocationActivity } from "@opencode/core/location-activity"
import { LocationServiceMap, type LocationServices } from "@opencode/core/location-services"
import { Project } from "@opencode/core/project"
import { ProjectTable } from "@opencode/core/project/sql"
import { AbsolutePath } from "@opencode/core/schema"
import { Session } from "@opencode/core/session"
import { SessionExecution } from "@opencode/core/session/execution"
import { SessionEvent } from "@opencode/core/session/event"
import { SessionRunner } from "@opencode/core/session/runner/index"
import { SessionTable } from "@opencode/core/session/sql"
import { Workspace } from "@opencode/core/workspace"
import { testEffect } from "./lib/effect"

// Nix supplies the expected timeout, not a configuration override for the tested service.
const idleTTL = Duration.fromInputUnsafe(process.env.OPENCODE_TEST_IDLE_TTL ?? "16 hours")
const margin = Duration.fromInputUnsafe("2 minutes")
const cleanup = Duration.sum(idleTTL, margin)
const longPause = Duration.max(idleTTL, Duration.fromInputUnsafe("17 hours"))
const ref = LocationServiceMap.canonical({ directory: AbsolutePath.make("/project") })
const sibling = Location.Ref.make({ directory: ref.directory, workspaceID: Workspace.ID.make("wrk_idle") })
const sessionID = Session.ID.make("ses_retention")
type Control = { question: boolean; started: Deferred.Deferred<void>; finish: Deferred.Deferred<void> }
const controls = new Map<string, Control>()

// Retain execution ownership, forms, jobs, and eviction. Replace only model work.
const locations = Layer.effect(LocationServiceMap.Service, Effect.gen(function* () {
  const bus = yield* Bus.Service
  const map = yield* LayerMap.make((location: Location.Ref) => Layer.mergeAll(
    Config.testLayer([]),
    Layer.succeed(Location.Service, Location.Service.of({
      directory: location.directory, workspaceID: location.workspaceID,
      project: { id: Project.ID.global, directory: location.directory, canonical: location.directory },
    })),
    Layer.effect(SessionRunner.Service, Effect.gen(function* () {
      const forms = yield* Form.Service
      return SessionRunner.Service.of({ drain: ({ sessionID }) => Effect.gen(function* () {
        const control = controls.get(sessionID)!
        yield* Deferred.succeed(control.started, undefined)
        if (control.question) yield* forms.ask({ sessionID, title: "Continue?", fields: [{ key: "answer", type: "string" }] }).pipe(Effect.orDie)
        else yield* Deferred.await(control.finish)
        return SessionRunner.DrainResult.Complete()
      }) })
    })),
  ).pipe(
    Layer.provideMerge(Form.layer), Layer.provide(Layer.succeed(Bus.Service, bus)), Layer.fresh,
  ) as unknown as Layer.Layer<LocationServices, FileSystem.DirectoryNotFoundError>, { idleTimeToLive: Duration.infinity })
  return {
    ...map,
    get: (ref: Location.Ref) => map.get(LocationServiceMap.canonical(ref)),
    contextEffect: (ref: Location.Ref) => map.contextEffect(LocationServiceMap.canonical(ref)),
    contextEffectOption: (ref: Location.Ref) => map.contextEffectOption(LocationServiceMap.canonical(ref)),
    invalidate: (ref: Location.Ref) => map.invalidate(LocationServiceMap.canonical(ref)),
  }
}))

const it = testEffect(AppNodeBuilder.build(LayerNode.group([
  Database.node, Bus.node, Job.node, LocationServiceMap.node, SessionExecution.node, LocationActivity.node,
]), [LocationServiceMap.node.replace(makeGlobalNode({ service: LocationServiceMap.Service, layer: locations, deps: [Bus.node] }))]))

const prepare = Effect.gen(function* () {
  const db = (yield* Database.Service).db
  const map = yield* LocationServiceMap.Service
  yield* db.insert(ProjectTable).values({ id: Project.ID.global, worktree: ref.directory, sandboxes: [] }).run().pipe(Effect.orDie)
  yield* db.insert(SessionTable).values({ id: sessionID, project_id: Project.ID.global, slug: "retention",
    directory: ref.directory, title: "Retention", version: "test" }).run().pipe(Effect.orDie)
  const context = yield* map.contextEffect(ref).pipe(Effect.scoped)
  yield* Location.Service.pipe(Effect.provide(map.get(sibling)), Effect.scoped)
  return { map, forms: Context.get(context, Form.Service) }
})

describe("location retention", () => {
  for (const question of [false, true]) for (const stop of [false, true])
    it.effect(`${question ? "question" : "quiet execution"} survives idle sweeps and remains ${stop ? "interruptible" : "completable"}`, () => Effect.gen(function* () {
      const { map, forms } = yield* prepare
      const execution = yield* SessionExecution.Service
      const bus = yield* Bus.Service
      const control = { question, started: yield* Deferred.make<void>(), finish: yield* Deferred.make<void>() }
      controls.set(sessionID, control)
      yield* Effect.addFinalizer(() => Effect.sync(() => controls.delete(sessionID)))
      const created = yield* Deferred.make<Form.Info>()
      const interrupted: unknown[] = []
      const unsubscribe = yield* bus.listen((event) => Effect.gen(function* () {
        if (event.type === SessionEvent.Execution.Interrupted.type) interrupted.push(event.data)
        if (event.type === Form.Event.Created.type) yield* Deferred.succeed(created, Schema.decodeUnknownSync(Form.Event.Created.data)(event.data).form)
      }))
      yield* Effect.addFinalizer(() => unsubscribe)
      const running = yield* execution.resume(sessionID).pipe(Effect.exit, Effect.forkScoped)
      yield* Deferred.await(control.started)
      const pending = question ? yield* Deferred.await(created) : undefined
      yield* TestClock.adjust("1 minute")
      for (const delay of [Duration.fromInputUnsafe("62 minutes"), longPause]) {
        yield* TestClock.adjust(delay)
        expect(Array.from(yield* RcMap.keys(map.rcMap))).toContainEqual(ref)
        expect(yield* execution.active).toEqual(new Set([sessionID]))
        expect(interrupted).toEqual([])
        expect((yield* forms.list()).map((form) => form.id)).toEqual(pending ? [pending.id] : [])
      }
      // The idle sibling expires while the occupied location remains usable.
      expect(Array.from(yield* RcMap.keys(map.rcMap))).toEqual([ref])
      if (stop) expect(yield* execution.interrupt(sessionID)).toBe(true)
      else if (pending) yield* forms.reply({ id: pending.id, answer: { answer: "continue" } })
      else yield* Deferred.succeed(control.finish, undefined)
      expect((yield* Fiber.join(running))._tag).toBe(stop ? "Failure" : "Success")
      expect(yield* execution.active).toEqual(new Set())
      yield* TestClock.adjust(cleanup)
      expect(Array.from(yield* RcMap.keys(map.rcMap))).toEqual([])
    }))

  for (const owner of ["shell", "child", "parent"] as const)
    it.effect(`background ${owner} protects its location until completion`, () => Effect.gen(function* () {
      const { map } = yield* prepare
      const jobs = yield* Job.Service
      const finish = yield* Deferred.make<string>()
      const missing = Session.ID.make("ses_missing")
      const job = yield* jobs.start({ id: "job_retention", type: owner === "shell" ? "shell" : "subagent",
        recovery: owner === "shell" ? { kind: "shell", sessionID, shellID: "retention", command: "sleep 9999" } : {
          kind: "subagent", parentSessionID: owner === "parent" ? sessionID : missing,
          childSessionID: owner === "child" ? sessionID : missing, agent: "general", description: "Retention",
        }, run: Deferred.await(finish) })
      yield* jobs.background(job.id)
      yield* TestClock.adjust("1 minute")
      for (const delay of [Duration.fromInputUnsafe("62 minutes"), longPause]) {
        yield* TestClock.adjust(delay)
        expect(Array.from(yield* RcMap.keys(map.rcMap))).toContainEqual(ref)
        expect((yield* jobs.get(job.id))?.status).toBe("running")
      }
      expect(Array.from(yield* RcMap.keys(map.rcMap))).toEqual([ref])
      yield* Deferred.succeed(finish, "done")
      yield* jobs.wait({ id: job.id })
      yield* TestClock.adjust(cleanup)
      expect(Array.from(yield* RcMap.keys(map.rcMap))).toEqual([])
    }))

  it.effect("default idle TTL matches the configured package value", () => Effect.gen(function* () {
    const map = yield* LocationServiceMap.Service
    yield* Location.Service.pipe(Effect.provide(map.get(ref)), Effect.scoped)
    yield* TestClock.adjust("1 minute")
    const half = Duration.times(idleTTL, 0.5)
    yield* TestClock.adjust(half)
    expect(Array.from(yield* RcMap.keys(map.rcMap))).toEqual([ref])
    yield* TestClock.adjust(Duration.sum(half, margin))
    expect(Array.from(yield* RcMap.keys(map.rcMap))).toEqual([])
  }))
})
