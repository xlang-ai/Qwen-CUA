import { OperatorConsole } from "./ui/OperatorConsole";

export default function Home() {
  const runnerBaseUrl =
    process.env.NEXT_PUBLIC_RUNNER_BASE_URL ?? "http://127.0.0.1:4001";
  return <OperatorConsole runnerBaseUrl={runnerBaseUrl} />;
}

