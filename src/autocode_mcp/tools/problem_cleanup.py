"""Generator process & checkpoint cleanup tool (``problem_cleanup_processes``)."""

from __future__ import annotations

from ..runtime_store import GENERATE_CHECKPOINT, get_section, update_section
from ..utils.process import is_pid_alive, terminate_pid_tree
from .base import Tool, ToolResult, input_schema_from_model
from .problem_helpers import _facade_attr
from .schemas import ProblemCleanupProcessesInput


class ProblemCleanupProcessesTool(Tool):
    """清理 problem_generate_tests 残留状态和进程。"""

    @property
    def name(self) -> str:
        return "problem_cleanup_processes"

    @property
    def description(self) -> str:
        return "巡检并回收生成器残留进程（默认执行），仅按当前问题记录的 PID 精准清理。"

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(ProblemCleanupProcessesInput)

    async def execute(self, problem_dir: str, kill_all_generators: bool = True) -> ToolResult:
        # 默认即巡检并回收残留 PID（不再直接返回 success）；回收前用 psutil 校验存活，
        # 已退出的 PID 跳过不报错，POSIX 下按进程组整树回收。
        state = self._load_cleanup_state(problem_dir) or {}
        removed_files: list[str] = []
        pids = state.get("active_pids", []) if isinstance(state, dict) else []
        if not isinstance(pids, list):
            pids = []
        valid_pids = [pid for pid in pids if isinstance(pid, int) and pid > 0]
        try:
            killed: list[int] = []
            skipped: list[int] = []
            failed: list[dict[str, str | int]] = []
            for pid in valid_pids:
                if not _facade_attr("is_pid_alive", is_pid_alive)(pid):
                    # 已退出的残留 PID：跳过且不报错。
                    skipped.append(pid)
                    continue
                ok, err = await _facade_attr("terminate_pid_tree", terminate_pid_tree)(pid)
                if ok:
                    killed.append(pid)
                else:
                    failed.append({"pid": pid, "stdout": "", "stderr": err})
            # 仅保留仍失败的 PID 供重试；已杀与已退出的均从状态移除。
            failed_pid_set = {int(item["pid"]) for item in failed}
            remaining_pids = [pid for pid in valid_pids if pid in failed_pid_set]
            self._write_cleanup_state(problem_dir, remaining_pids)
            return ToolResult.ok(
                removed_files=removed_files,
                killed_pids=killed,
                skipped_pids=skipped,
                failed_pids=failed,
                warning="No tracked generator PID found; nothing to reclaim" if not valid_pids else "",
                message="Cleanup finished",
            )
        except Exception as exc:
            return ToolResult.fail(f"cleanup failed: {exc}", removed_files=removed_files)

    def _load_cleanup_state(self, problem_dir: str) -> dict | None:
        state = get_section(problem_dir, GENERATE_CHECKPOINT)
        return state if isinstance(state, dict) else None

    def _write_cleanup_state(self, problem_dir: str, remaining_pids: list[int]) -> None:
        update_section(problem_dir, GENERATE_CHECKPOINT, {"active_pids": remaining_pids})

