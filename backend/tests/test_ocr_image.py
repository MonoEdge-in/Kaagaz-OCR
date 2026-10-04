"""How the OCR image installs Paddle (backend/Dockerfile.ocr, security A6).

The image is never built here (no Docker in CI), so these read the files as text:
the build argument, the GPU wheel's pin and hash, and the GPU constraints file,
against pyproject.toml's extras and constraints-ocr.txt, and the layer order that
keeps the Paddle install and the weights cached.
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


def stages() -> tuple[str, str]:
    """Dockerfile.ocr's two stages as text: the pins stage, then the image itself."""
    _, pins_stage, image_stage = read("Dockerfile.ocr").split("\nFROM ")
    return pins_stage, image_stage


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
    # One default, before the first FROM, taken up by both stages.
    arguments = re.findall(r"^ARG PADDLE_EXTRA.*", text, re.MULTILINE)
    assert arguments == ["ARG PADDLE_EXTRA=paddle", "ARG PADDLE_EXTRA", "ARG PADDLE_EXTRA"]
    pins_stage, text = stages()
    refusal = 'echo "PADDLE_EXTRA must be paddle or paddle-gpu, not: $PADDLE_EXTRA" >&2; exit 1 ;;'
    assert refusal in pins_stage and refusal in text
    assert 'case "$PADDLE_EXTRA" in' in text
    branches = re.findall(r"^      (\S+)\) \\$", text, re.MULTILINE)
    assert branches == ["paddle", "paddle-gpu", "*"]
    other = text[text.index("      *) \\") : text.index("    esac")]
    assert "exit 1" in other
    froms = re.findall(r"^FROM (\S+)(.*)$", read("Dockerfile.ocr"), re.MULTILINE)
    assert [suffix for _, suffix in froms] == [" AS pins", ""]  # the pins stage, then the image
    assert froms[0][0] == froms[1][0]  # one base image, for both stages and both builds
    assert re.fullmatch(r"python:3\.12\.14-slim-trixie@sha256:[0-9a-f]{64}", froms[0][0])


def test_the_gpu_path_installs_hashed_files_only() -> None:
    _, text = stages()
    gpu = text[text.index("      paddle-gpu) \\") : text.index("      *) \\")]
    first, second = gpu.split("&& pip install", 1)  # step 1, the GPU wheel; step 2, PyPI
    assert "--no-index --no-deps --require-hashes --requirement paddle-gpu-wheel.txt" in first
    assert "--no-deps --require-hashes --index-url https://pypi.org/simple" in second
    assert "--requirement constraints-ocr-gpu.txt" in second
    assert "pip check" in gpu
    # What went in is the paddle-gpu extra's pins, as the pins stage wrote them.
    assert "open('/pins/paddle-gpu.txt').read().splitlines()]" in gpu
    assert "assert pins and not bad, bad" in gpu
    assert "('nvidia-cudnn-cu12' in names) == gpu" in text  # and no CPU paddlepaddle beside it
    # The build never imports paddle: the GPU paddle needs the driver, which a build has not.
    assert "import paddle" not in text and "is_compiled_with_cuda" not in text


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
    for pin in extras["paddle"]:  # one line each in /pins/<extra>.txt, split on '==' by the GPU check
        assert re.fullmatch(r"[a-z0-9][a-z0-9._-]*(\[[a-z0-9-]+\])?==[0-9][0-9.]*", pin), pin
    assert extras["paddle-gpu"] == swapped
    assert "paddlepaddle-gpu==3.3.1" in extras["paddle-gpu"]  # the wheel's version


def test_the_weights_are_downloaded_before_our_own_code_is_copied() -> None:
    """A change to kaagaz/ must not download the weights again (the layer cache, EC2 PRD WP-D)."""
    lines = code_lines("Dockerfile.ocr")
    image_from = next(line for line in lines if line.startswith("FROM ") and " AS " not in line)
    lines = lines[lines.index(image_from) :]  # the image's stage only
    install = next(i for i, line in enumerate(lines) if line.startswith('RUN case "$PADDLE_EXTRA" in'))
    weights = next(i for i, line in enumerate(lines) if line.startswith("RUN hf download PaddlePaddle/"))
    weights_end = lines.index("    && chmod -R a-w /opt/models")  # the same step's last line
    code = lines.index("COPY kaagaz kaagaz")
    assert install < weights < weights_end < code < lines.index("RUN pip install --no-deps .")
    # `hf` comes from the Paddle install, and the step needs no file copied after it.
    assert [line for line in lines[:weights] if line.startswith("COPY ")] == [
        "COPY constraints-ocr.txt constraints-ocr-gpu.txt paddle-gpu-wheel.txt ./",
        "COPY --from=pins /pins /pins",
    ]
    assert weights_end < lines.index("COPY pyproject.toml ./") < code


def test_only_the_extras_pins_reach_the_paddle_install() -> None:
    """A pyproject.toml edit elsewhere must not install Paddle again (EC2 disk, 4 Oct 2026).

    The pins stage copies pyproject.toml alone and writes the chosen extra's pins; the image
    copies that file only, and Docker keys a COPY by content, so the install stays cached.
    """
    pins_stage, image_stage = stages()
    pins_lines = [line for line in pins_stage.splitlines() if line.startswith(("COPY ", "RUN "))]
    assert pins_lines == ["COPY pyproject.toml ./", 'RUN case "$PADDLE_EXTRA" in \\']
    assert "      paddle | paddle-gpu) \\\n        mkdir /pins \\\n" in pins_stage
    assert (
        "['project']['optional-dependencies']; print(*extras[sys.argv[1]], sep='\\n')\" "
        '"$PADDLE_EXTRA" > "/pins/$PADDLE_EXTRA.txt" ;;'
    ) in pins_stage
    assert "pip " not in pins_stage  # nothing is installed there
    cpu = image_stage[image_stage.index("      paddle) \\") : image_stage.index("      paddle-gpu) \\")]
    assert "--constraint constraints-ocr.txt --requirement /pins/paddle.txt fastapi uvicorn pillow ;;" in cpu
    assert "tomllib" not in image_stage  # the image reads the pins file, not pyproject.toml


def test_the_image_keeps_its_security_checks() -> None:
    _, text = stages()
    for check in (
        "--no-index --no-deps --require-hashes --requirement paddle-gpu-wheel.txt",
        "&& pip check \\\n",
        "| sha256sum -c - \\\n",
        '&& find /opt/models -name "*.py" -delete \\\n',
        "&& chmod -R a-w /opt/models\n",
        "for m in ('transformers', 'psycopg', 'boto3', 'sqlalchemy') if u.find_spec(m)]; assert not bad",
        "assert ('paddlepaddle' in names, 'paddlepaddle-gpu' in names) == (not gpu, gpu)",
        "\nRUN useradd --uid 10001 --no-create-home --home-dir /tmp ocr\nUSER 10001\n",
    ):
        assert text.count(check) == 1, check
    assert text.count("--require-hashes") == 2  # the GPU wheel and every PyPI file
    downloads = re.findall(r"^(?:RUN|    &&) hf download PaddlePaddle/", text, re.MULTILINE)
    assert len(downloads) == 2  # both models, each with its sha256 below
    assert len(re.findall(r"^        \"[0-9a-f]{64}  /opt/models/", text, re.MULTILINE)) == 2
