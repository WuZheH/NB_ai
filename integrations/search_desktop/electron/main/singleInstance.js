const SHOW_ACTION = "show";
const TEST_QUIT_ACTION = "fully_quit";

export function createSingleInstanceData({ windowMode = {}, argv = [], pid = process.pid } = {}) {
  return Object.freeze({
    action: windowMode.testMode === true && argv.includes("--search-test-quit")
      ? TEST_QUIT_ACTION
      : SHOW_ACTION,
    requesterPid: Number(pid),
  });
}

export function resolveSecondInstanceAction({ windowMode = {}, additionalData = {} } = {}) {
  if (windowMode.testMode === true && additionalData.action === TEST_QUIT_ACTION) {
    return TEST_QUIT_ACTION;
  }
  return SHOW_ACTION;
}

export async function waitForRequesterExit(
  pid,
  {
    isProcessRunning = defaultIsProcessRunning,
    timeoutMs = 10000,
    pollIntervalMs = 25,
  } = {},
) {
  const requesterPid = Number(pid);
  if (!Number.isInteger(requesterPid) || requesterPid <= 0) {
    throw new Error("search_test_quit_requester_pid_invalid");
  }
  const deadline = Date.now() + timeoutMs;
  while (isProcessRunning(requesterPid)) {
    if (Date.now() >= deadline) {
      throw new Error("search_test_quit_requester_exit_timeout");
    }
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
  }
}

function defaultIsProcessRunning(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    if (error?.code === "EPERM") return true;
    if (error?.code === "ESRCH") return false;
    throw error;
  }
}
