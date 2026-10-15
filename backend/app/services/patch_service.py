"""Apply bounded, single-file unified patches without executing repository code."""
import ast
import json
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PatchError(ValueError):
    pass


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or str(path) != value or
            any(p in ('..', '.', '.git') for p in path.parts) or
            any(c in value for c in ('\\', '\x00', '\n', '\r', '\t', ' '))):
        raise PatchError('Unsafe patch path')
    # Changes to execution policy or credentials need a separate manual workflow.
    if value.startswith('.github/workflows/') or any(p == '.env' or p.startswith('.env.') for p in path.parts):
        raise PatchError('Workflow and secret-file changes require manual review')
    return value


class Classification(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    classification: Literal['bug', 'feature', 'question', 'documentation', 'spam', 'out_of_scope']
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    is_actionable: bool
    reasoning: str = ''
    suggested_action: str = ''


class Change(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    file: str
    description: str = ''
    code_diff: str = Field(min_length=1, max_length=100_000)

    @field_validator('file')
    @classmethod
    def check_path(cls, value):
        return safe_path(value)


class Solution(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    analysis: str = ''
    solution_approach: str = ''
    files_to_modify: list[str] = Field(default_factory=list, max_length=10)
    primary_language: str = ''
    changes: list[Change] = Field(min_length=1, max_length=10)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    testing_notes: str = ''
    potential_risks: str = ''


def apply_patch(original: str | None, diff: str, file_path: str) -> str:
    """Only modifications/new regular text files; no deletes, renames, modes or binaries."""
    safe_path(file_path)
    if len(diff.encode()) > 100_000 or (original is not None and len(original.encode()) > 500_000):
        raise PatchError('Patch or original exceeds size limit')
    lines = diff.splitlines()
    old = [s for s in lines if s.startswith('--- ')]
    new = [s for s in lines if s.startswith('+++ ')]
    expected_old = '/dev/null' if original is None else f'a/{file_path}'
    if old != [f'--- {expected_old}'] or new != [f'+++ b/{file_path}']:
        raise PatchError('Patch must contain exactly the requested file with canonical a/b headers')
    if any(s.startswith(('rename ', 'copy ', 'old mode ', 'new mode ', 'new file mode ', 'deleted file mode ', 'GIT binary')) for s in lines):
        raise PatchError('Only text content changes are supported')
    if any(s.startswith('diff ') and s != f'diff --git a/{file_path} b/{file_path}' for s in lines):
        raise PatchError('Unexpected diff target')
    with tempfile.TemporaryDirectory(prefix='devinbox-patch-') as tmp:
        root = Path(tmp)
        target = root / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if original is not None:
            target.write_bytes(original.encode('utf-8'))
        # No shell, hooks, repo config, network, or model-supplied command execution.
        env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
        subprocess.run(['git', 'init', '-q', tmp], check=True, env=env, capture_output=True, timeout=10)
        for check in (True, False):
            cmd = ['git', 'apply', '--whitespace=nowarn'] + (['--check'] if check else []) + ['-']
            result = subprocess.run(cmd, input=diff.encode(), cwd=tmp, env=env, capture_output=True, timeout=10)
            if result.returncode:
                raise PatchError('Patch does not apply cleanly to the pinned source')
        if not target.is_file() or target.is_symlink():
            raise PatchError('Patch did not produce a regular file')
        content = target.read_bytes().decode('utf-8')
        if len(content.encode()) > 500_000 or content == original:
            raise PatchError('Patch is empty or exceeds the output limit')
        try:
            if file_path.endswith('.py'):
                ast.parse(content, filename=file_path)
            elif file_path.endswith('.json'):
                json.loads(content)
        except (SyntaxError, ValueError) as e:
            raise PatchError('Generated file failed syntax validation') from e
        return content
