"""How the OCR image installs Paddle (backend/Dockerfile.ocr, security A6).

The image is never built here (no Docker in CI), so these read the files as text:
the build argument, the GPU wheel's pin and hash, and the GPU constraints file,
against pyproject.toml's extras and constraints-ocr.txt.
"""

import re
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
GPU_WHEEL = re.compile(
    r"paddlepaddle-gpu @ https://paddle-whl\.cdn\.bcebos\.com/stable/cu126/paddlepaddle-gpu/"
    r"paddlepaddle_gpu-3\.3\.1-cp312-cp312-linux_x86_64\.whl --hash=sha256:[0-9a-f]{64}"
)
NVIDIA = {
    "nvidia-cublas-cu12",
    "nvidia-cuda-cccl-cu12",
    "nvidia-cuda-cupti-cu12",
    "nvidia-cuda-nvrtc-cu12",
    "nvidia-cuda-runtime-cu12",
    "nvidia-cudnn-cu12",
    "nvidia-cufft-cu12",
    "nvidia-cufile-cu12",
    "nvidia-curand-cu12",
    "nvidia-cusolver-cu12",
    "nvidia-cusparse-cu12",
    "nvidia-cusparselt-cu12",
    "nvidia-nccl-cu12",
    "nvidia-nvjitlink-cu12",
    "nvidia-nvtx-cu12",
}


def read(name: str) -> str:
    return (BACKEND / name).read_text(encoding="utf-8")


def code_lines(name: str) -> list[str]:
    """The file's lines that are not comments or blank."""
    return [line for line in read(name).splitlines() if line.strip() and not line.lstrip().startswith("#")]


def pins(name: str) -> dict[str, str]:
    """name -> version for each ``name==version`` requirement in a constraints file."""
    found = re.findall(r"^([a-z0-9][a-z0-9._-]*)==(\S+)", read(name), re.MULTILINE)
    return dict(found)


def gpu_requirements() -> list[tuple[str, list[str]]]:
    """(requirement, its hashes) for each entry of constraints-ocr-gpu.txt."""
    entries: list[tuple[str, list[str]]] = []
    for line in code_lines("constraints-ocr-gpu.txt"):
        if not line.startswith(" "):
            entries.append((line.removesuffix(" \\"), []))
        elif "--hash=" in line:
            entries[-1][1].append(line.strip().removesuffix(" \\"))
    return entries


def test_the_build_argument_picks_cpu_by_default_and_refuses_anything_else() -> None:
    text = read("Dockerfile.ocr")
    assert re.search(r"^ARG PADDLE_EXTRA=paddle$", text, re.MULTILINE)
    assert 'case "$PADDLE_EXTRA" in' in text
    branches = re.findall(r"^      (\S+)\) \\$", text, re.MULTILINE)
    assert branches == ["paddle", "paddle-gpu", "*"]
    other = text[text.index("      *) \\") : text.index("    esac")]
    assert "exit 1" in other
    assert len(re.findall(r"^FROM ", text, re.MULTILINE)) == 1  # one base image for both


def test_the_gpu_path_installs_hashed_files_only() -> None:
    text = read("Dockerfile.ocr")
    gpu = text[text.index("      paddle-gpu) \\") : text.index("      *) \\")]
    first, second = gpu.split("&& pip install", 1)  # step 1, the GPU wheel; step 2, PyPI
    assert "--no-index --no-deps --require-hashes --requirement paddle-gpu-wheel.txt" in first
    assert "--no-deps --require-hashes --index-url https://pypi.org/simple" in second
    assert "--requirement constraints-ocr-gpu.txt" in second
    assert "pip check" in gpu
    assert "paddle.is_compiled_with_cuda() == gpu" in text  # and no CPU paddlepaddle beside it


def test_no_second_index_anywhere() -> None:
    for name in ("Dockerfile.ocr", "constraints-ocr-gpu.txt", "paddle-gpu-wheel.txt", "constraints-ocr.txt"):
        code = "\n".join(code_lines(name))
        assert "--extra-index-url" not in code, name
        assert not re.search(r"(^|\s)(-f|--find-links|-i)\s", code), name


def test_the_gpu_wheel_is_one_hashed_direct_url() -> None:
    assert [GPU_WHEEL.fullmatch(line) is not None for line in code_lines("paddle-gpu-wheel.txt")] == [True]


def test_every_gpu_package_is_pinned_and_hashed() -> None:
    entries = gpu_requirements()
    assert entries
    for requirement, hashes in entries:
        assert re.fullmatch(r"[a-z0-9][a-z0-9._-]*==\S+", requirement), requirement
        assert hashes, requirement
        assert all(re.fullmatch(r"--hash=sha256:[0-9a-f]{64}", value) for value in hashes), requirement
    names = {requirement.split("==")[0] for requirement, _ in entries}
    assert "paddlepaddle" not in names  # the CPU build, which would overwrite the GPU one
    assert "paddlepaddle-gpu" not in names  # it comes from paddle-gpu-wheel.txt only


def test_the_gpu_image_differs_from_the_cpu_one_only_by_paddle_and_nvidia() -> None:
    cpu, gpu = pins("constraints-ocr.txt"), pins("constraints-ocr-gpu.txt")
    assert set(cpu) - set(gpu) == {"paddlepaddle"}
    assert set(gpu) - set(cpu) == NVIDIA
    assert {name: gpu[name] for name in cpu.keys() & gpu.keys()} == {
        name: cpu[name] for name in cpu.keys() & gpu.keys()
    }


def test_the_paddle_gpu_extra_is_the_paddle_extra_with_one_swap() -> None:
    with (BACKEND / "pyproject.toml").open("rb") as file:
        extras = tomllib.load(file)["project"]["optional-dependencies"]
    swapped = [pin.replace("paddlepaddle==", "paddlepaddle-gpu==") for pin in extras["paddle"]]
    assert extras["paddle-gpu"] == swapped
    assert "paddlepaddle-gpu==3.3.1" in extras["paddle-gpu"]  # the wheel's version
