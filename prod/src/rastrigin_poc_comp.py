import csv
import json
import math
import multiprocessing as mp
import queue
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

# =========================================================
# CONFIG: mexa aqui e só aperte F5
# =========================================================
# FIND (achar)
TOTAL = 1_000_000_000         # range [-TOTAL/2, +TOTAL/2)
THRESHOLD = 0.1               # em inteiros, f(0)=0 passa
MIN_RANGE_MODE = "per_worker" # "fixed" ou "per_worker"
MIN_RANGE_FIXED = 50_000_000  # usado se MIN_RANGE_MODE="fixed"
MAX_SPLITS = None             # None = ilimitado; ou int
RUN_FIND = True
RUN_FIND_SEQ_BASELINE = True  # roda o sequencial 1 vez como baseline

# BENCH (desempenho bruto)
RUN_BENCH = True
BENCH_SECONDS_LIST = list(range(10, 61, 10))  # 10,20,30,40,50,60
RUN_BENCH_SEQ_BASELINE = True  # roda baseline seq para cada tempo

# Sweep workers
WORKERS_FROM = 2
WORKERS_TO = 9  # inclusive

# Resultados
RESULTS_DIR = Path(__file__).resolve().parent / "results"
TAG = "poc_rastrigin_tree"

# Debug
PRINT_RANGE_CHECK = True
# =========================================================


# -----------------------
# Objective function
# -----------------------
def rastrigin_int(x: int) -> float:
    return (x * x) - 10.0 * math.cos(2.0 * math.pi * x) + 10.0


@dataclass
class FindResult:
    x: int
    fx: float
    elapsed_s: float
    attempts: int


# -----------------------
# FIND: Sequential baseline
# -----------------------
def find_sequential(start: int, end: int, threshold: float) -> Optional[FindResult]:
    t0 = time.perf_counter()
    attempts = 0
    for x in range(start, end):
        fx = rastrigin_int(x)
        attempts += 1
        if fx < threshold:
            dt = time.perf_counter() - t0
            return FindResult(x=x, fx=fx, elapsed_s=dt, attempts=attempts)
    return None


# -----------------------
# FIND: Worker scans leaf intervals (reports DONE once)
# -----------------------
def worker_scan_find(task_q: mp.Queue, result_q: mp.Queue, stop_evt: mp.Event, threshold: float):
    local_attempts = 0
    found_sent = False

    while True:
        if stop_evt.is_set():
            break

        try:
            item = task_q.get(timeout=0.2)
        except queue.Empty:
            continue

        if item is None:
            break

        a, b = item
        for x in range(a, b):
            if stop_evt.is_set():
                break
            fx = rastrigin_int(x)
            local_attempts += 1
            if fx < threshold:
                stop_evt.set()
                if not found_sent:
                    result_q.put(("FOUND", x, fx))
                    found_sent = True
                break

    result_q.put(("DONE", local_attempts))


def drain_results_find(result_q: mp.Queue, stop_evt: mp.Event, attempts: int, found, timeout_s: float):
    if timeout_s <= 0:
        while True:
            try:
                msg = result_q.get_nowait()
            except queue.Empty:
                break

            if msg[0] == "DONE":
                attempts += msg[1]
            elif msg[0] == "FOUND":
                _, x, fx = msg
                found = (x, fx)
                stop_evt.set()

        return attempts, found

    deadline = time.perf_counter() + timeout_s
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            break

        try:
            msg = result_q.get(timeout=remaining)
        except queue.Empty:
            break

        if msg[0] == "DONE":
            attempts += msg[1]
        elif msg[0] == "FOUND":
            _, x, fx = msg
            found = (x, fx)
            stop_evt.set()

    return attempts, found


# -----------------------
# FIND: Tree fragmentation (no heuristic) using fixed workers
# -----------------------
def find_fragment_tree(
    start: int,
    end: int,
    threshold: float,
    workers: int,
    min_range_size: int,
    max_splits: Optional[int],
) -> Optional[FindResult]:
    t0 = time.perf_counter()

    task_q = mp.Queue(maxsize=max(8, workers * 8))
    result_q = mp.Queue()
    stop_evt = mp.Event()

    procs = [
        mp.Process(target=worker_scan_find, args=(task_q, result_q, stop_evt, threshold))
        for _ in range(workers)
    ]
    for p in procs:
        p.start()

    expand_q: List[Tuple[int, int]] = [(start, end)]
    splits = 0

    attempts = 0
    found = None

    def drain(timeout: float):
        nonlocal attempts, found
        attempts, found = drain_results_find(result_q, stop_evt, attempts, found, timeout)

    # Scheduler
    while expand_q and not stop_evt.is_set():
        a, b = expand_q.pop(0)
        size = b - a

        if (max_splits is not None) and (splits >= max_splits):
            while not stop_evt.is_set():
                try:
                    task_q.put((a, b), timeout=0.05)
                    break
                except queue.Full:
                    drain(0.02)
            drain(0.0)
            continue

        if size <= min_range_size:
            while not stop_evt.is_set():
                try:
                    task_q.put((a, b), timeout=0.05)
                    break
                except queue.Full:
                    drain(0.02)
            drain(0.0)
        else:
            mid = (a + b) // 2
            expand_q.append((a, mid))
            expand_q.append((mid, b))
            splits += 1
            if splits % 256 == 0:
                drain(0.01)

    # close queue (do NOT set stop_evt here)
    for _ in range(workers):
        task_q.put(None)

    for p in procs:
        p.join()

    drain(0.2)

    dt = time.perf_counter() - t0
    if found:
        x, fx = found
        return FindResult(x=x, fx=fx, elapsed_s=dt, attempts=attempts)
    return None


# -----------------------
# BENCH: Sequential (same process)
# -----------------------
def bench_sequential(seconds: float, start_x: int = 0) -> Tuple[float, int, float]:
    t0 = time.perf_counter()
    attempts = 0
    x = start_x
    while (time.perf_counter() - t0) < seconds:
        rastrigin_int(x)
        attempts += 1
        x += 1
    dt = time.perf_counter() - t0
    ips = attempts / max(dt, 1e-9)
    return dt, attempts, ips


# -----------------------
# BENCH: Parallel workers (local loop)
# -----------------------
def worker_bench(seconds: float, result_q: mp.Queue, worker_id: int, start_x: int):
    t0 = time.perf_counter()
    attempts = 0
    x = start_x
    while (time.perf_counter() - t0) < seconds:
        rastrigin_int(x)
        attempts += 1
        x += 1
    result_q.put(("DONE", worker_id, attempts))


def bench_parallel(seconds: float, workers: int) -> Tuple[float, float, int, float, float]:
    """
    Returns:
      wall_dt, overhead_s, attempts, ips_effective, ips_compute
    """
    t0 = time.perf_counter()
    result_q = mp.Queue()

    procs = []
    for wid in range(workers):
        p = mp.Process(target=worker_bench, args=(seconds, result_q, wid, wid * 1_000_000))
        p.start()
        procs.append(p)

    total_attempts = 0
    done = 0
    while done < workers:
        msg = result_q.get()
        if msg[0] == "DONE":
            _, _, att = msg
            total_attempts += att
            done += 1

    for p in procs:
        p.join()

    wall_dt = time.perf_counter() - t0
    overhead_s = wall_dt - seconds
    ips_effective = total_attempts / max(wall_dt, 1e-9)
    ips_compute = total_attempts / max(seconds, 1e-9)
    return wall_dt, overhead_s, total_attempts, ips_effective, ips_compute


# -----------------------
# Results saving
# -----------------------
def save_results(rows: List[Dict[str, Any]], results_dir: Path, tag: str) -> Tuple[Path, Path]:
    results_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")

    json_path = results_dir / f"{tag}_{ts}.json"
    csv_path = results_dir / f"{tag}_{ts}.csv"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    keys = sorted({k for r in rows for k in r.keys()})
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)

    return json_path, csv_path


# -----------------------
# Plotting
# -----------------------
def plot_three(rows: List[Dict[str, Any]], results_dir: Path, tag: str, bench_plot_seconds: Optional[int] = None):
    """
    Gera 3 gráficos:
      1) FIND: Tempo (s) - SEQ vs TREE (baseline horizontal vs workers)
      2) FIND: Attempts     - SEQ vs TREE (baseline horizontal vs workers)
      3) BENCH: Speedup efetivo (PAR/SEQ) vs Workers (somente para bench_plot_seconds; default=max)
    """
    try:
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"(plot) Matplotlib indisponível, pulando plots: {e}")
        return None

    results_dir.mkdir(parents=True, exist_ok=True)

    # =========================
    # FIND: SEQ vs TREE (tempo e attempts)
    # =========================
    find_seq = next((r for r in rows if r.get("mode") == "find_seq"), None)
    find_tree = [r for r in rows if r.get("mode") == "find_tree" and r.get("workers") is not None]

    out_time = out_attempts = None

    if find_seq and find_seq.get("time_s") is not None and find_seq.get("attempts") is not None and find_tree:
        seq_time = float(find_seq["time_s"])
        seq_attempts = float(find_seq["attempts"])

        # ordena por workers e filtra incompletos
        find_tree_sorted = sorted(find_tree, key=lambda r: int(r["workers"]))
        ws = []
        tree_times = []
        tree_attempts = []

        for r in find_tree_sorted:
            if r.get("time_s") is None or r.get("attempts") is None:
                continue
            ws.append(int(r["workers"]))
            tree_times.append(float(r["time_s"]))
            tree_attempts.append(float(r["attempts"]))

        if ws:
            # Plot 1: Tempo SEQ vs TREE
            fig1 = plt.figure()
            ax1 = fig1.add_subplot(111)

            # baseline SEQ (linha horizontal)
            ax1.plot(ws, [seq_time] * len(ws), linestyle="--", marker=None, label="SEQ (baseline)")
            ax1.plot(ws, tree_times, marker="o", label="TREE")

            ax1.set_title("FIND: Tempo até achar (s) — SEQ vs TREE")
            ax1.set_xlabel("Workers")
            ax1.set_ylabel("Tempo (s)")
            ax1.set_yscale("log")
            ax1.grid(True)
            ax1.legend()

            out_time = results_dir / f"{tag}_find_tempo_seq_vs_tree.png"
            fig1.savefig(out_time, dpi=160, bbox_inches="tight")
            plt.close(fig1)

            # Plot 2: Attempts SEQ vs TREE
            fig2 = plt.figure()
            ax2 = fig2.add_subplot(111)

            ax2.plot(ws, [seq_attempts] * len(ws), linestyle="--", marker=None, label="SEQ (baseline)")
            ax2.plot(ws, tree_attempts, marker="o", label="TREE")

            ax2.set_title("FIND: Attempts até achar — SEQ vs TREE")
            ax2.set_xlabel("Workers")
            ax2.set_ylabel("Attempts")
            ax2.set_yscale("log")
            ax2.grid(True)
            ax2.legend()

            out_attempts = results_dir / f"{tag}_find_attempts_seq_vs_tree.png"
            fig2.savefig(out_attempts, dpi=160, bbox_inches="tight")
            plt.close(fig2)
        else:
            print("(plot) FIND: sem pontos válidos no find_tree para plotar.")
    else:
        print("(plot) FIND: baseline ou resultados insuficientes para plotar (find_seq/find_tree).")

    # =========================
    # BENCH: Speedup efetivo vs workers (1 tempo)
    # =========================
    bench_par = [r for r in rows if r.get("mode") == "bench_par"]
    bench_seq = [r for r in rows if r.get("mode") == "bench_seq"]

    out_bench = None

    if bench_par and bench_seq:
        available_secs = sorted({int(r["bench_seconds"]) for r in bench_par if r.get("bench_seconds") is not None})
        if not available_secs:
            print("(plot) BENCH: sem bench_seconds válidos.")
            return (out_time, out_attempts, None)

        if bench_plot_seconds is None:
            bench_plot_seconds = available_secs[-1]  # maior (ex.: 60)

        seq_row = next((r for r in bench_seq if int(r["bench_seconds"]) == int(bench_plot_seconds)), None)
        if not seq_row or seq_row.get("ips_effective") is None:
            print(f"(plot) BENCH: não achei baseline seq para {bench_plot_seconds}s.")
            return (out_time, out_attempts, None)

        ips_seq = float(seq_row["ips_effective"])

        bench_par_sel = [
            r for r in bench_par
            if int(r.get("bench_seconds")) == int(bench_plot_seconds) and r.get("workers") is not None
        ]
        bench_par_sel = sorted(bench_par_sel, key=lambda r: int(r["workers"]))

        ws_b = []
        speed_eff = []
        for r in bench_par_sel:
            if r.get("ips_effective") is None:
                continue
            ws_b.append(int(r["workers"]))
            speed_eff.append(float(r["ips_effective"]) / max(ips_seq, 1e-9))

        fig3 = plt.figure()
        ax3 = fig3.add_subplot(111)
        ax3.plot(ws_b, speed_eff, marker="o")
        ax3.set_title(f"BENCH: Speedup efetivo (PAR/SEQ) vs Workers ({bench_plot_seconds}s)")
        ax3.set_xlabel("Workers")
        ax3.set_ylabel("Speedup efetivo (PAR/SEQ)")
        ax3.grid(True)

        out_bench = results_dir / f"{tag}_bench_speedup_efetivo_{bench_plot_seconds}s_vs_workers.png"
        fig3.savefig(out_bench, dpi=160, bbox_inches="tight")
        plt.close(fig3)
    else:
        print("(plot) BENCH: resultados insuficientes para plotar (bench_seq/bench_par).")

    return (out_time, out_attempts, out_bench)




# -----------------------
# Main
# -----------------------
def main():
    start = -TOTAL // 2
    end = TOTAL // 2

    print(f"Range [{start}, {end}]  threshold={THRESHOLD}")
    if PRINT_RANGE_CHECK:
        print("0 está no range?", start <= 0 < end)

    rows: List[Dict[str, Any]] = []

    # -------- Baselines --------
    find_seq: Optional[FindResult] = None
    bench_seq_by_sec: Dict[int, Tuple[float, int, float]] = {}

    if RUN_FIND and RUN_FIND_SEQ_BASELINE:
        print("\n--- FIND: SEQ (baseline) ---")
        t0 = time.perf_counter()
        find_seq = find_sequential(start, end, THRESHOLD)
        if find_seq:
            print(f"SEQ x={find_seq.x} f={find_seq.fx:.6f} t={find_seq.elapsed_s:.2f}s attempts={find_seq.attempts:,}")
            rows.append({
                "mode": "find_seq",
                "workers": 1,
                "total": TOTAL,
                "threshold": THRESHOLD,
                "min_range_size": None,
                "time_s": find_seq.elapsed_s,
                "attempts": find_seq.attempts,
                "x": find_seq.x,
                "fx": find_seq.fx,
            })
        else:
            dt = time.perf_counter() - t0
            print("SEQ not found")
            rows.append({
                "mode": "find_seq",
                "workers": 1,
                "total": TOTAL,
                "threshold": THRESHOLD,
                "min_range_size": None,
                "time_s": dt,
                "attempts": None,
                "x": None,
                "fx": None,
            })

    if RUN_BENCH and RUN_BENCH_SEQ_BASELINE:
        print("\n--- BENCH: SEQ (baseline) ---")
        for sec in BENCH_SECONDS_LIST:
            dt1, att1, ips1 = bench_sequential(float(sec), start_x=0)
            bench_seq_by_sec[int(sec)] = (dt1, att1, ips1)
            print(f"BENCH SEQ {sec:>2}s: t={dt1:.2f}s attempts={att1:,} ips={ips1:,.0f}/s")
            rows.append({
                "mode": "bench_seq",
                "workers": 1,
                "bench_seconds": int(sec),
                "time_s": dt1,
                "attempts": att1,
                "ips_effective": ips1,
                "ips_compute": ips1,
                "overhead_s": 0.0,
            })

    # -------- Sweep workers --------
    print(f"\n=== SWEEP workers {WORKERS_FROM}..{WORKERS_TO} ===")
    for w in range(WORKERS_FROM, WORKERS_TO + 1):
        if MIN_RANGE_MODE == "per_worker":
            min_range_size = max(1, TOTAL // w)
        else:
            min_range_size = int(MIN_RANGE_FIXED)

        print(f"\n--- workers={w}  min_range_size={min_range_size} ---")

        # FIND: TREE
        if RUN_FIND:
            r = find_fragment_tree(start, end, THRESHOLD, w, min_range_size, MAX_SPLITS)
            if r:
                attempts_saving = None
                time_saving = None
                if find_seq and find_seq.attempts:
                    attempts_saving = 1.0 - (r.attempts / find_seq.attempts)
                    time_saving = 1.0 - (r.elapsed_s / find_seq.elapsed_s) if find_seq.elapsed_s > 0 else None

                print(f"FIND TREE: x={r.x} f={r.fx:.6f} t={r.elapsed_s:.2f}s attempts={r.attempts:,}")
                if attempts_saving is not None and time_saving is not None:
                    print(f"  economia_attempts={attempts_saving*100:.2f}%  economia_tempo={time_saving*100:.2f}%")

                rows.append({
                    "mode": "find_tree",
                    "workers": w,
                    "total": TOTAL,
                    "threshold": THRESHOLD,
                    "min_range_size": min_range_size,
                    "time_s": r.elapsed_s,
                    "attempts": r.attempts,
                    "x": r.x,
                    "fx": r.fx,
                    "attempts_saving_pct": (attempts_saving * 100.0) if attempts_saving is not None else None,
                    "time_saving_pct": (time_saving * 100.0) if time_saving is not None else None,
                })
            else:
                print("FIND TREE: not found")
                rows.append({
                    "mode": "find_tree",
                    "workers": w,
                    "total": TOTAL,
                    "threshold": THRESHOLD,
                    "min_range_size": min_range_size,
                    "time_s": None,
                    "attempts": None,
                    "x": None,
                    "fx": None,
                    "attempts_saving_pct": None,
                    "time_saving_pct": None,
                })

        # BENCH: PAR (seconds sweep)
        if RUN_BENCH:
            for sec in BENCH_SECONDS_LIST:
                wall_dt, overhead_s, att2, ips_eff, ips_comp = bench_parallel(float(sec), w)
                print(f"BENCH PAR w={w} {sec:>2}s: wall={wall_dt:.2f}s overhead~{overhead_s:.2f}s attempts={att2:,}")
                print(f"  ips_effective={ips_eff:,.0f}/s  ips_compute={ips_comp:,.0f}/s")

                ips_seq = bench_seq_by_sec.get(int(sec), (None, None, None))[2]
                if ips_seq is not None:
                    speedup_eff = ips_eff / max(ips_seq, 1e-9)
                    speedup_comp = ips_comp / max(ips_seq, 1e-9)
                    print(f"  speedup_effective={speedup_eff:.2f}x  speedup_compute={speedup_comp:.2f}x")
                else:
                    speedup_eff = None
                    speedup_comp = None

                rows.append({
                    "mode": "bench_par",
                    "workers": w,
                    "bench_seconds": int(sec),
                    "time_s": wall_dt,
                    "attempts": att2,
                    "overhead_s": overhead_s,
                    "ips_effective": ips_eff,
                    "ips_compute": ips_comp,
                    "speedup_effective_x": speedup_eff,
                    "speedup_compute_x": speedup_comp,
                })

    # -------- Save --------
    json_path, csv_path = save_results(rows, RESULTS_DIR, TAG)
    print("\n=== SALVO ===")
    print(f"JSON: {json_path}")
    print(f"CSV : {csv_path}")

    plots = plot_three(rows, RESULTS_DIR, TAG, bench_plot_seconds=max(BENCH_SECONDS_LIST))
    if plots:
      p1, p2, p3 = plots
      if p1: print(f"PNG: {p1}")
      if p2: print(f"PNG: {p2}")
      if p3: print(f"PNG: {p3}")



if __name__ == "__main__":
    main()
