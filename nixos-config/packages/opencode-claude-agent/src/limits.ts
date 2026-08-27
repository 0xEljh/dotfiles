const ACTIVE_LIMIT = 2
const QUEUE_LIMIT = 4
const QUEUE_TIMEOUT_MS = 30_000

type Waiter = {
  resolve: (release: () => void) => void
  reject: (error: Error) => void
  timer: ReturnType<typeof setTimeout>
  signal?: AbortSignal
  onAbort?: () => void
}

export class AdmissionController {
  private active = 0
  private readonly queue: Waiter[] = []

  constructor(
    private readonly activeLimit = ACTIVE_LIMIT,
    private readonly queueLimit = QUEUE_LIMIT,
    private readonly queueTimeoutMs = QUEUE_TIMEOUT_MS,
  ) {}

  async acquire(signal?: AbortSignal): Promise<() => void> {
    if (signal?.aborted) throw signal.reason
    if (this.active < this.activeLimit) {
      this.active += 1
      return this.releaseOnce()
    }
    if (this.queue.length >= this.queueLimit) {
      throw new Error("Claude Agent queue is full")
    }

    return new Promise<() => void>((resolve, reject) => {
      const waiter: Waiter = {
        resolve,
        reject,
        signal,
        timer: setTimeout(() => {
          this.remove(waiter)
          reject(new Error("Claude Agent queue wait timed out"))
        }, this.queueTimeoutMs),
      }
      waiter.onAbort = () => {
        this.remove(waiter)
        reject(signal?.reason ?? new Error("Claude Agent queue wait aborted"))
      }
      signal?.addEventListener("abort", waiter.onAbort, { once: true })
      this.queue.push(waiter)
    })
  }

  private remove(waiter: Waiter): void {
    const index = this.queue.indexOf(waiter)
    if (index >= 0) this.queue.splice(index, 1)
    clearTimeout(waiter.timer)
    if (waiter.onAbort) waiter.signal?.removeEventListener("abort", waiter.onAbort)
  }

  private releaseOnce(): () => void {
    let released = false
    return () => {
      if (released) return
      released = true
      const waiter = this.queue.shift()
      if (waiter) {
        clearTimeout(waiter.timer)
        if (waiter.onAbort) {
          waiter.signal?.removeEventListener("abort", waiter.onAbort)
        }
        waiter.resolve(this.releaseOnce())
        return
      }
      this.active -= 1
    }
  }
}

export const admission = new AdmissionController()

export function wrapStream<T>(
  stream: ReadableStream<T>,
  release: () => void,
  signal?: AbortSignal,
): ReadableStream<T> {
  const reader = stream.getReader()
  let onAbort: (() => void) | undefined
  let released = false
  const finish = () => {
    if (released) return
    released = true
    if (onAbort) signal?.removeEventListener("abort", onAbort)
    release()
  }
  return new ReadableStream<T>({
    start(controller) {
      onAbort = () => {
        void reader.cancel(signal?.reason).finally(finish)
        controller.error(signal?.reason ?? new Error("Claude Agent stream aborted"))
      }
      if (signal?.aborted) onAbort()
      else signal?.addEventListener("abort", onAbort, { once: true })
    },
    async pull(controller) {
      try {
        const next = await reader.read()
        if (next.done) {
          finish()
          controller.close()
          return
        }
        controller.enqueue(next.value)
      } catch (error) {
        finish()
        controller.error(error)
      }
    },
    async cancel(reason) {
      try {
        await reader.cancel(reason)
      } finally {
        finish()
      }
    },
  })
}
