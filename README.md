# Qwen-CUA

**Native Computer Use for (almost) Everything**

Qwen-CUA is a Qwen-based computer-use model and agent designed to operate
graphical interfaces through the same visual observations and native input
events available to a human. It observes screenshots, reasons over the visible
state, and acts with keyboard and mouse operations—without relying on DOM trees,
accessibility metadata, shell access, or task-specific APIs.

[Paper preview](./paper/Qwen-CUA-paper-preview.pdf) ·
[Run the demo](./demo/README.md)

> The full technical report is still in progress. This repository currently
> contains a one-page paper preview and the reference browser-agent demo.

## Model and agent

Qwen-CUA separates the learned computer-use policy from the runtime that safely
connects it to an interactive environment.

| Layer | Responsibility |
| --- | --- |
| **Qwen-CUA model** | Understand screenshots and instructions, track task progress, reason about the visible interface, and emit grounded keyboard/mouse actions. |
| **Agent runtime** | Capture observations, manage multimodal history, validate and execute actions, request operator approval, and preserve evidence for replay and verification. |

Together they form a native computer-use loop:

```text
instruction + screenshot
          ↓
    Qwen-CUA model
          ↓
  native action proposal
          ↓
 safety gate + execution
          ↓
 next screenshot / outcome
```

## Qwen-CUA model

### Native computer-use interface

The model operates from pixels and produces actions in a shared keyboard-and-
mouse action space. The same interface can transfer across browsers, desktop
applications, and websites because it does not expose hidden application state
or depend on a bespoke integration for every tool.

### Long-horizon interaction

Computer-use trajectories quickly accumulate image-heavy context. Qwen-CUA
retains recent visual evidence while folding older screenshots in chunks,
preserving earlier reasoning and actions. This keeps context growth bounded,
supports progress tracking, and leaves stable prefixes available for cache
reuse during extended workflows.

### Learning from verifiable experience

Qwen-CUA is trained through a closed loop that combines supervised fine-tuning,
reinforcement learning, large-scale environment rollouts, executable outcome
verification, and trajectory filtering. The training data includes controllable
web and desktop environments, state-grounded tasks, and personalized
long-horizon expert trajectories.

## Agent behavior

A useful computer-use agent must do more than predict the next click. The
Qwen-CUA agent is designed to:

- ground each decision in the current screenshot;
- preserve task state across long, multimodal trajectories;
- recover from failed actions and changing interfaces;
- check the resulting state instead of relying only on its own narration;
- combine native interaction with specialized tools when appropriate;
- surface sensitive operations for operator review.

The reference implementation in [`demo/`](./demo/README.md) provides a
browser-first agent runtime with an operator console, isolated Playwright
sessions, typed action validation, approval gates, deterministic local tasks,
and replay artifacts.

## Evaluation snapshot

The current paper preview evaluates Qwen-CUA in a pure computer-use setting
across eight benchmarks covering desktop control, long-horizon workflows,
personalized and professional tasks, web interaction, and adversarial
robustness. In the reported results, Qwen-CUA improves consistently over
Qwen3.7 and reaches **86.2 on OSWorld-Verified**.

See the [one-page paper preview](./paper/Qwen-CUA-paper-preview.pdf) for the
abstract and main results figure.

## Repository structure

```text
Qwen-CUA/
├── paper/    # Paper preview; full report forthcoming
├── demo/     # Runnable browser-agent reference implementation
├── LICENSE
└── README.md
```

## Demo

The demo is intentionally self-contained. Start with its own documentation:

```bash
cd demo
cp .env.example .env
```

Then follow [`demo/README.md`](./demo/README.md) for native and Docker setup,
configuration, the action protocol, safety boundaries, and development checks.
The repository does not contain API credentials or model weights.

## Safety

Computer-use agents can make mistakes, encounter prompt injection, and trigger
consequential interface actions. Use isolated browser contexts, avoid
authenticated or high-stakes workflows, and require human approval for
sensitive operations. A model declaring success is not proof that the intended
real-world outcome was achieved.

## License

Apache-2.0. See [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
