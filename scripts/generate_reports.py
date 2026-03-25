import argparse
import datetime
import json
import os
from typing import Any, Dict, List, Tuple

from core.database.connection import get_db, execute, fetch_all
from core.services.metrics_service import log_guardrail_alert


def _parse_date(date_str: str) -> Tuple[int, int]:
    tz = datetime.timezone(datetime.timedelta(hours=8))
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=tz)
    start_ts = int(dt.timestamp())
    end_ts = start_ts + 86400
    return start_ts, end_ts


def _economy_summary(start_ts: int, end_ts: int) -> Dict[str, Any]:
    rows = fetch_all(
        """SELECT module,
                  SUM(delta_copper) AS copper,
                  SUM(delta_gold) AS gold,
                  SUM(delta_exp) AS exp,
                  SUM(delta_stamina) AS stamina
           FROM economy_ledger
           WHERE ts >= ? AND ts < ?
           GROUP BY module""",
        (start_ts, end_ts),
    )
    total = {"copper": 0, "gold": 0, "exp": 0, "stamina": 0}
    modules: Dict[str, Any] = {}
    for row in rows:
        modules[row["module"]] = {
            "copper": int(row.get("copper", 0) or 0),
            "gold": int(row.get("gold", 0) or 0),
            "exp": int(row.get("exp", 0) or 0),
            "stamina": int(row.get("stamina", 0) or 0),
        }
        total["copper"] += modules[row["module"]]["copper"]
        total["gold"] += modules[row["module"]]["gold"]
        total["exp"] += modules[row["module"]]["exp"]
        total["stamina"] += modules[row["module"]]["stamina"]
    return {"total": total, "by_module": modules}


def _action_funnels(start_ts: int, end_ts: int) -> Dict[str, Any]:
    rows = fetch_all(
        """SELECT event,
                  COUNT(1) AS total,
                  SUM(success) AS success_count
           FROM event_logs
           WHERE ts >= ? AND ts < ?
           GROUP BY event""",
        (start_ts, end_ts),
    )
    funnels: Dict[str, Any] = {}
    for row in rows:
        total = int(row.get("total", 0) or 0)
        success_count = int(row.get("success_count", 0) or 0)
        funnels[row["event"]] = {
            "total": total,
            "success": success_count,
            "success_rate": round(success_count / total, 4) if total else 0.0,
        }
    return funnels


def _top_net_users(start_ts: int, end_ts: int, limit: int = 20) -> Dict[str, List[Dict[str, Any]]]:
    rows = fetch_all(
        """SELECT user_id,
                  SUM(delta_copper) AS copper,
                  SUM(delta_gold) AS gold,
                  SUM(delta_exp) AS exp
           FROM economy_ledger
           WHERE ts >= ? AND ts < ?
           GROUP BY user_id""",
        (start_ts, end_ts),
    )
    def _sort_key(field: str):
        return sorted(rows, key=lambda r: int(r.get(field, 0) or 0), reverse=True)[:limit]

    def _pack(items, field: str):
        return [{"user_id": r["user_id"], field: int(r.get(field, 0) or 0)} for r in items]

    return {
        "top_copper": _pack(_sort_key("copper"), "copper"),
        "top_gold": _pack(_sort_key("gold"), "gold"),
        "top_exp": _pack(_sort_key("exp"), "exp"),
    }


def _inventory_snapshot(item_ids: List[str]) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {}
    for item_id in item_ids:
        rows = fetch_all(
            """SELECT user_id, SUM(quantity) AS qty
               FROM items
               WHERE item_id = ?
               GROUP BY user_id""",
            (item_id,),
        )
        quantities = sorted([int(r.get("qty", 0) or 0) for r in rows])
        if not quantities:
            snapshot[item_id] = {"p50": 0, "p90": 0, "p99": 0}
            continue
        def _pct(p: float) -> int:
            idx = max(0, min(len(quantities) - 1, int(round(p * (len(quantities) - 1)))))
            return int(quantities[idx])
        snapshot[item_id] = {"p50": _pct(0.5), "p90": _pct(0.9), "p99": _pct(0.99)}
    return snapshot


def _gacha_health(start_ts: int, end_ts: int) -> Dict[str, Any]:
    rows = fetch_all(
        """SELECT banner_id, rarity, COUNT(1) AS c
           FROM gacha_logs
           WHERE created_at >= ? AND created_at < ?
           GROUP BY banner_id, rarity""",
        (start_ts, end_ts),
    )
    result: Dict[str, Any] = {}
    for row in rows:
        banner = str(row.get("banner_id"))
        result.setdefault(banner, {})
        result[banner][row.get("rarity")] = int(row.get("c", 0) or 0)
    return result


def _safe_json(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _is_truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _meta_rate(start_ts: int, end_ts: int, event: str, key: str) -> Dict[str, Any]:
    rows = fetch_all(
        "SELECT meta_json FROM event_logs WHERE ts >= %s AND ts < %s AND event = %s",
        (start_ts, end_ts, event),
    )
    total = 0
    hit = 0
    for row in rows:
        meta = _safe_json(row.get("meta_json"))
        if key not in meta:
            continue
        total += 1
        if _is_truthy(meta.get(key)):
            hit += 1
    rate = round(hit / total, 4) if total else 0.0
    return {"total": total, "hit": hit, "rate": rate}


def _event_meta_summary(start_ts: int, end_ts: int) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    summary["hunt_victory"] = _meta_rate(start_ts, end_ts, "hunt", "victory")
    summary["secret_realm_victory"] = _meta_rate(start_ts, end_ts, "secret_realm_explore", "victory")
    summary["resource_convert_success"] = _meta_rate(start_ts, end_ts, "resource_convert", "success")
    summary["enhance_success"] = _meta_rate(start_ts, end_ts, "enhance", "enhance_success")
    summary["breakthrough_success"] = _meta_rate(start_ts, end_ts, "breakthrough", "breakthrough_success")

    rows = fetch_all(
        "SELECT meta_json FROM event_logs WHERE ts >= %s AND ts < %s AND event = %s",
        (start_ts, end_ts, "pvp_challenge"),
    )
    counts = {"win": 0, "loss": 0, "draw": 0}
    for row in rows:
        meta = _safe_json(row.get("meta_json"))
        outcome = str(meta.get("outcome", "") or "").lower()
        if outcome in counts:
            counts[outcome] += 1
    total = sum(counts.values())
    summary["pvp_outcomes"] = {
        "total": total,
        "win": counts["win"],
        "loss": counts["loss"],
        "draw": counts["draw"],
        "win_rate": round(counts["win"] / total, 4) if total else 0.0,
    }
    return summary


def _gacha_expected_rates() -> Dict[str, Dict[str, float]]:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "data", "gacha.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        return {}
    expected: Dict[str, Dict[str, float]] = {}
    for banner in data.get("banners", []) or []:
        banner_id = str(banner.get("banner_id"))
        pools = banner.get("pools", []) or []
        rates: Dict[str, float] = {}
        for pool in pools:
            rarity = str(pool.get("rarity") or "")
            if not rarity:
                continue
            rates[rarity] = float(pool.get("rate", 0.0) or 0.0)
        if rates:
            expected[banner_id] = rates
    return expected


def _guardrails(
    report_date: str,
    economy: Dict[str, Any],
    funnels: Dict[str, Any],
    event_meta: Dict[str, Any],
    gacha: Dict[str, Any],
) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    expected_gacha = _gacha_expected_rates()

    def _emit(metric: str, value: float, lower: float | None = None, upper: float | None = None, detail: Dict[str, Any] | None = None) -> None:
        alerts.append({"metric": metric, "value": value, "lower": lower, "upper": upper, "detail": detail or {}})
        log_guardrail_alert(
            report_date=report_date,
            metric=metric,
            value=value,
            lower=lower,
            upper=upper,
            detail=detail,
        )

    total_copper = economy.get("total", {}).get("copper", 0)
    sources = fetch_all(
        """SELECT SUM(delta_copper) AS s
           FROM economy_ledger
           WHERE ts >= ? AND ts < ? AND delta_copper > 0""",
        _parse_date(report_date),
    )
    sinks = fetch_all(
        """SELECT SUM(delta_copper) AS s
           FROM economy_ledger
           WHERE ts >= ? AND ts < ? AND delta_copper < 0""",
        _parse_date(report_date),
    )
    src_val = float((sources[0].get("s", 0) or 0)) if sources else 0.0
    sink_val = abs(float((sinks[0].get("s", 0) or 0))) if sinks else 0.0
    ratio = (src_val / sink_val) if sink_val > 0 else 0.0
    if sink_val > 0 and (ratio < 0.9 or ratio > 1.15):
        _emit("copper_net_ratio", ratio, lower=0.9, upper=1.15, detail={"source": src_val, "sink": sink_val, "total": total_copper})

    shop = funnels.get("shop_buy") or {}
    if shop:
        fail_rate = 1.0 - float(shop.get("success_rate", 1.0) or 1.0)
        if fail_rate > 0.01:
            _emit("shop_buy_fail_rate", fail_rate, upper=0.01, detail={"total": shop.get("total", 0)})

    hunt_rate = event_meta.get("hunt_victory", {})
    if hunt_rate.get("total", 0) >= 50 and hunt_rate.get("rate", 0.0) < 0.5:
        _emit("hunt_victory_rate", float(hunt_rate.get("rate", 0.0)), lower=0.5, detail=hunt_rate)

    realm_rate = event_meta.get("secret_realm_victory", {})
    if realm_rate.get("total", 0) >= 30 and realm_rate.get("rate", 0.0) < 0.4:
        _emit("secret_realm_victory_rate", float(realm_rate.get("rate", 0.0)), lower=0.4, detail=realm_rate)

    convert_rate = event_meta.get("resource_convert_success", {})
    if convert_rate.get("total", 0) >= 30 and convert_rate.get("rate", 0.0) < 0.4:
        _emit("resource_convert_success_rate", float(convert_rate.get("rate", 0.0)), lower=0.4, detail=convert_rate)

    enhance_rate = event_meta.get("enhance_success", {})
    if enhance_rate.get("total", 0) >= 30 and enhance_rate.get("rate", 0.0) < 0.5:
        _emit("enhance_success_rate", float(enhance_rate.get("rate", 0.0)), lower=0.5, detail=enhance_rate)

    breakthrough_rate = event_meta.get("breakthrough_success", {})
    if breakthrough_rate.get("total", 0) >= 20 and breakthrough_rate.get("rate", 0.0) < 0.05:
        _emit("breakthrough_success_rate", float(breakthrough_rate.get("rate", 0.0)), lower=0.05, detail=breakthrough_rate)

    # gacha SSR rates vs expected
    for banner_id, counts in gacha.items():
        total = sum(int(v or 0) for v in counts.values())
        if total < 50:
            continue
        expected_rates = expected_gacha.get(str(banner_id)) or {}
        expected_ssr = float(expected_rates.get("SSR", 0.0) or 0.0)
        if expected_ssr <= 0:
            continue
        actual_ssr = float(counts.get("SSR", 0) or 0) / float(total)
        lower = expected_ssr * 0.5
        upper = expected_ssr * 1.5
        if actual_ssr < lower or actual_ssr > upper:
            _emit(
                f"gacha_ssr_rate:{banner_id}",
                actual_ssr,
                lower=lower,
                upper=upper,
                detail={"expected": expected_ssr, "total": total, "counts": counts},
            )

    return alerts


def _upsert_report(report_date: str, report_type: str, data: Dict[str, Any]) -> None:
    execute(
        """INSERT INTO daily_reports(report_date, report_type, data_json, created_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(report_date, report_type)
           DO UPDATE SET data_json = excluded.data_json, created_at = excluded.created_at""",
        (
            report_date,
            report_type,
            json.dumps(data, ensure_ascii=False),
            int(datetime.datetime.now().timestamp()),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d"))
    args = parser.parse_args()

    report_date = args.date
    start_ts, end_ts = _parse_date(report_date)
    _ = get_db()  # ensure db connection

    economy = _economy_summary(start_ts, end_ts)
    funnels = _action_funnels(start_ts, end_ts)
    event_meta = _event_meta_summary(start_ts, end_ts)
    top_users = _top_net_users(start_ts, end_ts)
    inventory = _inventory_snapshot([
        "iron_ore",
        "herb",
        "spirit_stone",
        "spirit_herb",
        "demon_core",
        "recipe_fragment",
        "dragon_scale",
        "phoenix_feather",
    ])
    gacha = _gacha_health(start_ts, end_ts)
    alerts = _guardrails(report_date, economy, funnels, event_meta, gacha)

    _upsert_report(report_date, "economy_total", economy)
    _upsert_report(report_date, "action_funnels", funnels)
    _upsert_report(report_date, "event_meta_summary", event_meta)
    _upsert_report(report_date, "top_net_users", top_users)
    _upsert_report(report_date, "inventory_snapshot", inventory)
    _upsert_report(report_date, "gacha_health", gacha)
    _upsert_report(report_date, "guardrail_alerts", {"alerts": alerts})

    print(json.dumps({
        "date": report_date,
        "economy_total": economy,
        "action_funnels": funnels,
        "event_meta_summary": event_meta,
        "top_net_users": top_users,
        "inventory_snapshot": inventory,
        "gacha_health": gacha,
        "guardrail_alerts": alerts,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
