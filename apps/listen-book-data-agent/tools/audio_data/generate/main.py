"""数据生成入口。"""

from __future__ import annotations

import argparse

from .config import GENERATION_DEFAULTS, generation_profile
from .db import close_db, init_db, interrupt_db
from .layers.layer1 import Layer1Generator
from .layers.layer2 import Layer2Generator
from .layers.layer3 import Layer3Generator
from .layers.layer4 import Layer4Generator
from .layers.layer5 import Layer5Generator
from .layers.layer6 import Layer6Generator
from .layers.validations import validate_acceptance
from .progress import console_print, progress_context

GENERATORS = (
    Layer1Generator,
    Layer2Generator,
    Layer3Generator,
    Layer4Generator,
    Layer5Generator,
    Layer6Generator,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audio data generator")
    parser.add_argument(
        "--profile",
        choices=("smoke", "full"),
        default="full",
        help="generation profile",
    )
    parser.add_argument(
        "--layers",
        nargs="+",
        type=int,
        choices=tuple(range(1, 7)),
        help="only run selected layers",
    )
    return parser.parse_args()


def run_generators(selected_layers: set[int] | None = None) -> None:
    for generator_cls in GENERATORS:
        if selected_layers and generator_cls.layer not in selected_layers:
            continue
        generator_cls().run()


def run_acceptance() -> None:
    console_print("\n" + "=" * 64)
    console_print("验收检查")
    console_print("=" * 64)
    for check in validate_acceptance():
        console_print(f"  [OK] acceptance: {check}")


def main() -> None:
    args = parse_args()
    interrupted = False
    init_db()
    try:
        with generation_profile(args.profile):
            with progress_context():
                console_print(
                    f"Generation profile: {args.profile} -> {GENERATION_DEFAULTS}"
                )
                selected_layers = set(args.layers) if args.layers else None
                run_generators(selected_layers)
                if selected_layers is None:
                    run_acceptance()
    except KeyboardInterrupt:
        interrupted = True
        console_print(
            "\nGeneration interrupted by user, interrupting database connection..."
        )
        interrupt_db()
        raise SystemExit(130)
    finally:
        if not interrupted:
            close_db()


if __name__ == "__main__":
    main()
