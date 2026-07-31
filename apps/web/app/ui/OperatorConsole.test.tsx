import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OperatorConsole } from "./OperatorConsole";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("OperatorConsole", () => {
  it("shows runner guidance while initial data is loading", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));
    render(<OperatorConsole runnerBaseUrl="http://runner.test" />);
    expect(
      screen.getByRole("heading", { name: "Runner offline" }),
    ).toBeInTheDocument();
    expect(screen.getByText("New browser run")).toBeInTheDocument();
  });
});
