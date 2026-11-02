#!/usr/bin/env bash
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
containerlab destroy -t "$ROOT/build/ft-core/ft-core.clab.yml" --cleanup
