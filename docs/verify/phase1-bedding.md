# Phase 1 verification — bedding core

Date: 2026-11-09. SCIA Engineer 24.0.3015.64, on a copy of a kademuur model last saved in v19.1 (32 piles,
319 line supports). `llsm bedding` was run through the CLI; the numbers below are read back from the saved file.

## Hand calculation, pile S4

Pile from z −1.1 to −19.5 (L = 18.4 m), vertical, D = 0.45 m, start level NAP −1.1, datum offset 0.
k_line = kh · D; written value = k_line · 1000 (N/m²).

| Segment | Layer (NAP) | On the pile | x1 = (1.1 + z)/−18.4 … by hand | x2 by hand | k by hand [N/m²] | Read back | Result |
|---|---|---|---|---|---|---|---|
| `LLSM:S4:1` | −2…−8, kh 5000, klei | −2…−8 | 0.9/18.4 = 0.048913 | 6.9/18.4 = 0.375000 | 5000 · 0.45 · 1000 = 2.25e6 | 0.048913, 0.375000, 2.25e6 (kY and kZ), member id 6 | PASS |
| `LLSM:S4:2` | −8…−14, kh 8000, zand, kleiig | −8…−14 | 0.375000 | 12.9/18.4 = 0.701087 | 3.6e6 | 0.375000, 0.701087, 3.6e6, member id 6 | PASS |
| `LLSM:S4:3` | −14…−20, kh 12000, zand | −14…−19.5 (clipped at the toe) | 0.701087 | 18.4/18.4 = 1.000000 | 5.4e6 | 0.701087, 1.000000, 5.4e6, member id 6 | PASS |

The member id is the pile's own: S4 is id 6, which SCIA itself reports when it resolves the name.

## Behaviour

| Check | Result | Evidence |
|---|---|---|
| Preview writes nothing | PASS | Folder listing unchanged after `preview`. |
| Pile with other supports refused | PASS | S4 carries 10; apply without `--allow-existing` stops with exit 1, nothing written. |
| Apply with `--allow-existing` | PASS | 3 of 3 confirmed, `model_llsm-01.esa`, 37.4 s. |
| Apply again on `_llsm-01` without `--replace` | PASS | Skipped, "Nothing to write", exit 1. |
| With `--replace` | PASS | 3 of 3 confirmed, `_llsm-02`, 31.7 s; all read-back supports identical to `_llsm-01` (the files differ by revision stamps). |
| Original unchanged | PASS | SHA-256 of the source and the working copy unchanged (4175E2AF…). |
| `other_changes` | PASS | Empty in every run. Supports 319 to 322, members unchanged. |
| All 32 piles at once (`--piles "S*"`) | PASS | 96 of 96 confirmed, 46.5 s. |
| Minimal `.def` | PASS | `src/llsm/scia/resources/line_supports.xml.def` (9.5 kB) is enough. |
| MCP tools | PASS | Both registered with explicit parameters; `bedding_preview` called through FastMCP's own dispatch. `bedding_apply` shares the code run above but was not called through MCP. |
| Live pytest (`LLSM_LIVE=1`) | PASS | 2 passed in 102 s. |

## Reading the member id

Each pile's id is stored twice in its record (30 and 236 bytes after the `_` marker that follows the geometry). The
reader needs both to agree. Checked against SCIA itself by writing one support per pile and reading the id SCIA
resolved from the name: 32 of 32 on the v19.1 file, 32 of 32 on the v24 save, and the single pile of the known model.

## Still open

- **Code manager 100→0.** The Phase 0 comparison on `_llsm-01` again flags the same tables as before: the line supports,
  the members (+12 bytes for three supports), the counters and revision log, and that one value in the code manager
  table. It is not explained; identify it before a pilot.
- **Existing supports are counted, not positioned.** The real model's own supports use a record layout whose positions
  are not decoded, so `--allow-existing` warns that stiffness adds up but cannot say where.
- **Not run:** the model open in SCIA during `apply` (the lock check is unit-tested only), and a stiffness check in
  SCIA's own panel on these segments.

## Tests

`pytest tests/unit`: 123 passed, 1 failed. The failure, `test_credentials.py::test_delete_clears`, is the known
environment leak listed for Phase 2.
