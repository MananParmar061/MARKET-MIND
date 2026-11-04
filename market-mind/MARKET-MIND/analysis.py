"""Offline price-file analysis. Pure Python, no internet. Expects the oldest row first."""
import csv
import io
import statistics as st


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def summarize_csv(raw: bytes) -> str:
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", "ignore")))
    reader.fieldnames = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    rows = []
    for r in reader:
        o, h, l, c, v = (_num(r.get(k)) for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c, v):
            continue
        stamp = next((r[k] for k in ("datetime", "timestamp", "date", "time") if r.get(k)), f"row {len(rows) + 1}")
        rows.append((stamp, o, h, l, c, v))
    if len(rows) < 5:
        return ("Could not analyse this CSV. It needs the columns date, open, high, low, close and volume, "
                "with at least 5 valid rows.")

    vols = [r[5] for r in rows]
    total_vol = sum(vols)
    mean_vol, sd_vol = st.mean(vols), st.pstdev(vols)
    vwap = sum((r[2] + r[3] + r[4]) / 3 * r[5] for r in rows) / total_vol if total_vol else None
    first, last = rows[0], rows[-1]
    change = (last[4] / first[4] - 1) * 100 if first[4] else 0.0
    hi = max(rows, key=lambda r: r[2])
    lo = min(rows, key=lambda r: r[3])
    spikes = sorted((r for r in rows if sd_vol and r[5] > mean_vol + 2 * sd_vol), key=lambda r: -r[5])[:5]
    delta = sum(r[5] * (r[4] - r[1]) / (r[2] - r[3]) for r in rows if r[2] > r[3])

    out = [
        f"Price file summary ({len(rows)} bars, {first[0]} to {last[0]}; assumes oldest row first)",
        f"First close {first[4]:,.2f}, last close {last[4]:,.2f}, change {change:+.2f}%",
        f"Period high {hi[2]:,.2f} on {hi[0]}, period low {lo[3]:,.2f} on {lo[0]}",
        (f"Last close is {'above' if last[4] > vwap else 'below'} the period VWAP of {vwap:,.2f}"
         if vwap else "VWAP unavailable (zero volume)"),
        f"Average volume {mean_vol:,.0f} per bar",
    ]
    if spikes:
        out.append("Volume spikes (more than 2 standard deviations above average):")
        out += [f"- {r[0]}: volume {r[5]:,.0f} ({r[5] / mean_vol:.1f}x average), close {r[4]:,.2f}" for r in spikes]
    else:
        out.append("No volume spikes above 2 standard deviations.")
    out.append(f"Approximate net volume delta {delta:+,.0f} "
               "(estimated from bar shapes; true order flow needs tick data)")
    return "\n".join(out)
