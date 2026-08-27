import { describe, expect, test, vi } from "vitest"

import { AdmissionController, wrapStream } from "../src/limits.js"

describe("query admission", () => {
  test("bounds active and queued queries", async () => {
    const admission = new AdmissionController(2, 2, 1_000)
    const releaseFirst = await admission.acquire()
    const releaseSecond = await admission.acquire()
    const third = admission.acquire()
    const fourth = admission.acquire()

    await expect(admission.acquire()).rejects.toThrow("queue is full")
    releaseFirst()
    const releaseThird = await third
    releaseSecond()
    const releaseFourth = await fourth
    releaseThird()
    releaseFourth()
  })

  test("removes an aborted queued query", async () => {
    const admission = new AdmissionController(1, 1, 1_000)
    const release = await admission.acquire()
    const controller = new AbortController()
    const queued = admission.acquire(controller.signal)

    controller.abort(new Error("cancelled"))
    await expect(queued).rejects.toThrow("cancelled")
    release()

    const releaseNext = await admission.acquire()
    releaseNext()
  })
})

describe("stream limits", () => {
  test("releases a stream slot when the request is aborted", async () => {
    const controller = new AbortController()
    const release = vi.fn()
    const source = new ReadableStream({ pull: () => new Promise(() => {}) })

    const wrapped = wrapStream(source, release, controller.signal)
    const read = wrapped.getReader().read()
    controller.abort(new Error("cancelled"))

    await expect(read).rejects.toThrow("cancelled")
    expect(release).toHaveBeenCalledTimes(1)
  })

  test("releases a stream slot only once after normal completion", async () => {
    const release = vi.fn()
    const source = new ReadableStream({
      start(controller) {
        controller.enqueue("result")
        controller.close()
      },
    })

    await wrapStream(source, release).pipeTo(new WritableStream())

    expect(release).toHaveBeenCalledTimes(1)
  })
})
