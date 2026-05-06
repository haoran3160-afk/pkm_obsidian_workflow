import { expect } from "vitest";
import * as matchers from "@testing-library/jest-dom/matchers";

expect.extend(matchers);

class EventSourceMock {
  onmessage: ((event: MessageEvent<string>) => void) | null = null;

  close() {}
}

Object.defineProperty(globalThis, "EventSource", {
  writable: true,
  value: EventSourceMock
});
