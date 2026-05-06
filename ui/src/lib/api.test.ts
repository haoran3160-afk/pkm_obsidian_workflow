import { buildLogsUrl } from "./api";

describe("api helpers", () => {
  it("builds the SSE log endpoint", () => {
    expect(buildLogsUrl()).toBe("/api/logs/stream");
  });
});
