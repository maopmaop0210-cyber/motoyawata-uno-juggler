from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

STORE_NAME = "本八幡UNO"
PEKASEN_URL = "https://pekasen.com/store/uno-motoyawata"
DMM_URL = "https://p-town.dmm.com/shops/chiba/3971/jackpot"

# BIG / REG / 合算 / ブドウの設定別目安。
# ブドウは機種や算出法によって参考値の性格が強いので、画面でも「参考」と表示します。
MACHINES: dict[str, dict[int, tuple[float, float, float, float]]] = {
    "マイジャグラーV": {
        1: (273.1, 409.6, 163.8, 5.91), 2: (270.8, 385.5, 159.1, 5.87),
        3: (266.4, 336.1, 148.6, 5.83), 4: (254.0, 290.0, 135.4, 5.80),
        5: (240.1, 268.6, 126.8, 5.76), 6: (229.1, 229.1, 114.6, 5.67),
    },
    "ネオアイムジャグラーEX": {
        1: (273.1, 439.8, 168.5, 6.02), 2: (269.7, 399.6, 161.0, 6.02),
        3: (269.7, 331.0, 148.6, 6.02), 4: (259.0, 315.1, 142.2, 6.02),
        5: (259.0, 255.0, 128.5, 6.02), 6: (255.0, 255.0, 127.5, 5.80),
    },
    "ゴーゴージャグラー3": {
        1: (259.0, 354.2, 149.6, 6.25), 2: (258.0, 332.7, 145.3, 6.20),
        3: (257.0, 306.2, 139.7, 6.15), 4: (254.0, 268.6, 130.5, 6.07),
        5: (247.3, 247.3, 123.7, 6.00), 6: (234.9, 234.9, 117.4, 5.92),
    },
    "ファンキージャグラー2": {
        1: (266.4, 439.8, 165.9, 5.94), 2: (259.0, 407.1, 158.3, 5.92),
        3: (256.0, 366.1, 150.7, 5.88), 4: (249.2, 322.8, 140.6, 5.83),
        5: (240.1, 299.3, 133.2, 5.76), 6: (219.9, 262.1, 119.6, 5.67),
    },
    "ミスタージャグラー": {
        1: (268.6, 374.5, 156.4, 6.24), 2: (267.5, 354.3, 152.4, 6.18),
        3: (260.1, 331.0, 145.6, 6.14), 4: (249.2, 291.3, 134.3, 6.10),
        5: (240.9, 257.0, 124.4, 6.06), 6: (237.4, 237.4, 118.7, 6.02),
    },
    "ウルトラミラクルジャグラー": {
        1: (267.5, 425.6, 164.2, 5.93), 2: (261.1, 402.1, 158.3, 5.93),
        3: (256.0, 350.5, 147.9, 5.93), 4: (242.7, 322.8, 138.6, 5.93),
        5: (233.2, 297.9, 130.8, 5.87), 6: (216.3, 277.7, 121.6, 5.81),
    },
    "ジャグラーガールズSS": {
        1: (273.1, 381.0, 159.1, 6.01), 2: (270.8, 350.5, 152.8, 6.01),
        3: (260.1, 316.6, 142.8, 6.01), 4: (250.1, 281.3, 132.4, 6.01),
        5: (243.6, 270.8, 128.3, 5.92), 6: (226.0, 252.1, 119.2, 5.89),
    },
}

ALIASES = {
    "マイジャグラーV": ["マイジャグラーV", "マイジャグラーⅤ"],
    "ネオアイムジャグラーEX": ["ネオアイムジャグラーEX", "ネオアイム"],
    "ゴーゴージャグラー3": ["ゴーゴージャグラー3", "ゴージャグ3"],
    "ファンキージャグラー2": ["ファンキージャグラー2", "ファンキー2"],
    "ミスタージャグラー": ["ミスタージャグラー", "ミスター"],
    "ウルトラミラクルジャグラー": ["ウルトラミラクルジャグラー", "ウルミラ"],
    "ジャグラーガールズSS": ["ジャグラーガールズSS", "ジャグラーガールズ"],
}

@dataclass
class Snapshot:
    machine_name: str
    rows: pd.DataFrame
    source_date: str
    source_url: str


def norm(s: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s))).lower()


def to_int(s: Any) -> int | None:
    if s is None:
        return None
    x = unicodedata.normalize("NFKC", str(s)).replace(",", "").replace("＋", "+").replace("−", "-")
    m = re.search(r"[-+]?\d+", x)
    return int(m.group()) if m else None


def to_den(s: Any) -> float | None:
    if s is None:
        return None
    x = unicodedata.normalize("NFKC", str(s))
    m = re.search(r"1\s*/\s*([\d.]+)", x)
    return float(m.group(1)) if m else None


def _heading_before(table) -> str:
    h = table.find_previous(["h2", "h3", "h4", "h5"])
    if h:
        return h.get_text(" ", strip=True)
    # サイト側が見出しタグを変えても拾えるよう直前の文字列も確認
    for prev in table.find_all_previous(limit=12):
        txt = prev.get_text(" ", strip=True) if hasattr(prev, "get_text") else ""
        if "台" in txt and len(txt) < 80:
            return txt
    return ""


def parse_pekasen_html(html: str, machine_name: str) -> Snapshot:
    soup = BeautifulSoup(html, "html.parser")
    aliases = ALIASES.get(machine_name, [machine_name])
    chosen = None

    for table in soup.find_all("table"):
        headers = [norm(th.get_text(" ", strip=True)) for th in table.find_all("th")]
        header_blob = "|".join(headers)
        if not ("台番" in header_blob and ("g数" in header_blob or "g数" in norm(header_blob)) and "big" in header_blob and "reg" in header_blob):
            continue
        heading = _heading_before(table)
        heading_n = norm(heading)
        if any(norm(a) in heading_n for a in aliases):
            chosen = table
            break

    if chosen is None:
        # 見出しとの紐付けに失敗した場合、機種名の要素から次の表を探す
        target = None
        for txt in soup.find_all(string=True):
            t = norm(txt)
            if any(norm(a) == t or norm(a) in t for a in aliases):
                target = txt.parent
                break
        if target:
            for table in target.find_all_next("table", limit=4):
                headers = [norm(th.get_text(" ", strip=True)) for th in table.find_all("th")]
                hb = "|".join(headers)
                if "台番" in hb and "big" in hb and "reg" in hb:
                    chosen = table
                    break

    if chosen is None:
        raise RuntimeError(f"{machine_name} の台データ表を見つけられませんでした。")

    rows = []
    header_cells = [th.get_text(" ", strip=True) for th in chosen.find_all("th")]
    hnorm = [norm(x) for x in header_cells]

    def idx_of(*keys: str) -> int | None:
        for i, h in enumerate(hnorm):
            if any(norm(k) in h for k in keys):
                return i
        return None

    i_no = idx_of("台番")
    i_g = idx_of("G数", "ゲーム")
    i_big = idx_of("BIG")
    i_reg = idx_of("REG")
    i_diff = idx_of("差枚")
    i_grape = idx_of("ブドウ逆算", "ぶどう逆算")

    # thead/tbody構造に依存しない
    for tr in chosen.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if not cells or cells == header_cells:
            continue
        if i_no is None or i_no >= len(cells):
            continue
        no = to_int(cells[i_no])
        if no is None or "平均" in cells[i_no]:
            continue
        def cell(i: int | None):
            return cells[i] if i is not None and i < len(cells) else None
        g = to_int(cell(i_g))
        big = to_int(cell(i_big))
        reg = to_int(cell(i_reg))
        diff = to_int(cell(i_diff))
        grape = to_den(cell(i_grape))
        if g is None or big is None or reg is None:
            continue
        rows.append({"台番号": no, "G数": g, "BIG": big, "REG": reg, "差枚": diff, "ぶどう": grape})

    if not rows:
        raise RuntimeError(f"{machine_name} の台データを解析できませんでした。")

    text = soup.get_text(" ", strip=True)
    m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", text)
    source_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else "不明"
    return Snapshot(machine_name, pd.DataFrame(rows).sort_values("台番号"), source_date, PEKASEN_URL)


def fetch_pekasen(machine_name: str) -> Snapshot:
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    r = requests.get(PEKASEN_URL, headers=headers, timeout=20, params={"_": int(datetime.now().timestamp())})
    r.raise_for_status()
    return parse_pekasen_html(r.text, machine_name)


def prob_den(games: int, count: int) -> float | None:
    return games / count if games > 0 and count > 0 else None


def nearest_setting(machine_name: str, metric_idx: int, observed: float | None) -> int | None:
    if observed is None or observed <= 0:
        return None
    spec = MACHINES[machine_name]
    return min(spec, key=lambda s: abs(math.log(observed / spec[s][metric_idx])))


def log_binom(n: int, k: float, p: float) -> float:
    if n <= 0 or k < 0 or k > n or not (0 < p < 1):
        return -1e18
    # k can be approximate for reverse-calculated grapes, so use gamma continuously.
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1) + k * math.log(p) + (n-k) * math.log(1-p)


def setting_likelihoods(machine_name: str, games: int, big: int, reg: int, grape_count: float | None) -> dict[int, float]:
    logs = {}
    for s, (bd, rd, _cd, gd) in MACHINES[machine_name].items():
        # REG重視、BIG中程度、ぶどうは逆算値なので補助扱い
        v = 0.65 * log_binom(games, big, 1/bd)
        v += 1.00 * log_binom(games, reg, 1/rd)
        if grape_count is not None:
            v += 0.30 * log_binom(games, grape_count, 1/gd)
        logs[s] = v
    m = max(logs.values())
    w = {s: math.exp(v-m) for s, v in logs.items()}
    z = sum(w.values()) or 1.0
    return {s: 100*w[s]/z for s in w}


def aggregate(df: pd.DataFrame) -> dict[str, Any]:
    games = int(df["G数"].sum())
    big = int(df["BIG"].sum())
    reg = int(df["REG"].sum())
    diff = int(df["差枚"].dropna().sum()) if df["差枚"].notna().any() else None

    grape_rows = df[df["ぶどう"].notna() & (df["ぶどう"] > 0)].copy()
    grape_count = None
    grape_den = None
    grape_games = 0
    if len(grape_rows):
        grape_games = int(grape_rows["G数"].sum())
        grape_count = float((grape_rows["G数"] / grape_rows["ぶどう"]).sum())
        if grape_count > 0:
            grape_den = grape_games / grape_count

    return {
        "games": games, "big": big, "reg": reg, "diff": diff,
        "big_den": prob_den(games, big), "reg_den": prob_den(games, reg),
        "combined_den": prob_den(games, big+reg), "grape_den": grape_den,
        "grape_count": grape_count, "grape_games": grape_games,
        "grape_machines": len(grape_rows),
    }


def fmt_den(v: float | None, digits: int = 1) -> str:
    return "—" if v is None else f"1/{v:.{digits}f}"


def add_row_estimates(df: pd.DataFrame, machine_name: str) -> pd.DataFrame:
    out = df.copy()
    out["BIG確率"] = [fmt_den(prob_den(g, b)) for g, b in zip(out["G数"], out["BIG"])]
    out["REG確率"] = [fmt_den(prob_den(g, r)) for g, r in zip(out["G数"], out["REG"])]
    out["合算"] = [fmt_den(prob_den(g, b+r)) for g, b, r in zip(out["G数"], out["BIG"], out["REG"])]
    out["ぶどう確率"] = [fmt_den(v, 2) if pd.notna(v) else "—" for v in out["ぶどう"]]
    settings = []
    for _, row in out.iterrows():
        gc = (row["G数"] / row["ぶどう"]) if pd.notna(row["ぶどう"]) and row["ぶどう"] > 0 else None
        probs = setting_likelihoods(machine_name, int(row["G数"]), int(row["BIG"]), int(row["REG"]), gc)
        settings.append(max(probs, key=probs.get))
    out["総合目安"] = [f"設定{s}相当" for s in settings]
    return out


def show_summary(df: pd.DataFrame, machine_name: str, title: str):
    a = aggregate(df)
    probs = setting_likelihoods(machine_name, a["games"], a["big"], a["reg"], a["grape_count"])
    overall = max(probs, key=probs.get)

    st.subheader(title)
    m1, m2 = st.columns(2)
    m1.metric("総回転", f"{a['games']:,} G")
    m2.metric("総差枚", "—" if a["diff"] is None else f"{a['diff']:+,} 枚")
    c1, c2 = st.columns(2)
    c1.metric("BIG", f"{a['big']}回", fmt_den(a["big_den"]))
    c2.metric("REG", f"{a['reg']}回", fmt_den(a["reg_den"]))
    c3, c4 = st.columns(2)
    c3.metric("合算", f"{a['big'] + a['reg']}回", fmt_den(a["combined_den"]))
    c4.metric("ぶどう（逆算）", fmt_den(a["grape_den"], 2), f"{a['grape_machines']}台分")

    st.markdown(f"### 総合目安：設定{overall}相当")
    if a["games"] < 4000:
        st.warning("サンプルが少ないため設定推測はかなりブレます。")

    metric_df = pd.DataFrame([
        ["BIG", fmt_den(a["big_den"]), f"設定{nearest_setting(machine_name,0,a['big_den'])}相当"],
        ["REG", fmt_den(a["reg_den"]), f"設定{nearest_setting(machine_name,1,a['reg_den'])}相当"],
        ["合算", fmt_den(a["combined_den"]), f"設定{nearest_setting(machine_name,2,a['combined_den'])}相当"],
        ["ぶどう", fmt_den(a["grape_den"],2), "判定不可" if a["grape_den"] is None else f"設定{nearest_setting(machine_name,3,a['grape_den'])}相当"],
    ], columns=["項目", "確率", "設定目安"])
    st.dataframe(metric_df, hide_index=True, use_container_width=True)

    st.markdown("#### 設定1〜6 相対期待度")
    p = pd.DataFrame({"設定": [f"設定{s}" for s in range(1,7)], "相対期待度": [probs[s] for s in range(1,7)]}).set_index("設定")
    st.bar_chart(p)

    return a, probs


# ---------------- UI ----------------
st.set_page_config(page_title="本八幡UNO ジャグラー判別", page_icon="🎰", layout="centered", initial_sidebar_state="collapsed")
st.markdown("""
<style>
.block-container{padding-top:1rem;padding-bottom:4rem;max-width:760px}
[data-testid="stMetric"]{border:1px solid rgba(128,128,128,.22);border-radius:14px;padding:10px}
.stButton>button{min-height:48px;border-radius:12px;font-weight:700}
@media(max-width:600px){
  .block-container{padding-left:.8rem;padding-right:.8rem}
  h1{font-size:1.75rem!important}
}
</style>
""", unsafe_allow_html=True)

st.title("🎰 本八幡UNO ジャグラー判別")
st.caption("スマホ・PC対応 / 機種と台番号を選んで更新するだけ")

machine_name = st.selectbox("機種名", list(MACHINES.keys()))
mode = st.radio("分析モード", ["1台", "範囲", "機種全体"], horizontal=True)

selected_no = None
start_no = None
end_no = None
if mode == "1台":
    selected_no = st.number_input("台番号", min_value=1, max_value=9999, value=658, step=1)
elif mode == "範囲":
    x, y = st.columns(2)
    with x:
        start_no = st.number_input("開始台番号", min_value=1, max_value=9999, value=658, step=1)
    with y:
        end_no = st.number_input("終了台番号", min_value=1, max_value=9999, value=670, step=1)

refresh = st.button("🔄 最新データを更新", type="primary", use_container_width=True)

if refresh:
    with st.spinner("本八幡UNOのデータを読み込んでいます…"):
        try:
            snap = fetch_pekasen(machine_name)
            st.session_state["snapshot"] = snap
            st.session_state["error"] = None
        except Exception as e:
            st.session_state["error"] = str(e)

if st.session_state.get("error"):
    st.error(st.session_state["error"])

snap: Snapshot | None = st.session_state.get("snapshot")
if snap and snap.machine_name == machine_name:
    st.caption(f"データ日付: {snap.source_date}　｜　取得時刻: {datetime.now().strftime('%H:%M:%S')}")
    base = snap.rows.copy()

    if mode == "1台":
        df = base[base["台番号"] == int(selected_no)].copy()
        if df.empty:
            st.warning(f"{machine_name} に {int(selected_no)}番台は見つかりませんでした。")
            st.caption("「機種全体」に切り替えると、この機種の台番号一覧を確認できます。")
        else:
            show_summary(df, machine_name, f"{int(selected_no)}番台")
            st.markdown("#### この台のデータ")
            display = add_row_estimates(df, machine_name)[["台番号","G数","BIG","REG","BIG確率","REG確率","合算","差枚","ぶどう確率","総合目安"]]
            st.dataframe(display, hide_index=True, use_container_width=True)

    elif mode == "範囲":
        lo, hi = sorted((int(start_no), int(end_no)))
        df = base[(base["台番号"] >= lo) & (base["台番号"] <= hi)].copy()
        if df.empty:
            st.warning(f"{lo}〜{hi}番台に {machine_name} のデータがありません。")
        else:
            show_summary(df, machine_name, f"{lo}〜{hi}番台（{len(df)}台）")
            st.markdown("#### 範囲内の差枚")
            chart = df[["台番号","差枚"]].dropna().set_index("台番号")
            if len(chart):
                st.bar_chart(chart)
            st.markdown("#### 台ごとの詳細")
            display = add_row_estimates(df, machine_name)[["台番号","G数","BIG","REG","合算","差枚","ぶどう確率","総合目安"]]
            st.dataframe(display, hide_index=True, use_container_width=True)

    else:
        df = base.copy()
        show_summary(df, machine_name, f"{machine_name} 全{len(df)}台")
        st.markdown("#### 全台の差枚")
        chart = df[["台番号","差枚"]].dropna().set_index("台番号")
        if len(chart):
            st.bar_chart(chart)
        st.markdown("#### 全台一覧")
        display = add_row_estimates(df, machine_name)[["台番号","G数","BIG","REG","合算","差枚","ぶどう確率","総合目安"]]
        st.dataframe(display, hide_index=True, use_container_width=True)

    with st.expander("設定別の基準値"):
        spec = MACHINES[machine_name]
        sdata = pd.DataFrame([
            {"設定":s, "BIG":fmt_den(v[0]), "REG":fmt_den(v[1]), "合算":fmt_den(v[2]), "ぶどう参考":fmt_den(v[3],2)}
            for s,v in spec.items()
        ])
        st.dataframe(sdata, hide_index=True, use_container_width=True)

    st.caption(f"データ取得元: {snap.source_url}")
else:
    st.info("機種・分析モードを選んで『最新データを更新』を押してください。")

st.divider()
st.caption("※ 設定推測は確率上の参考値で、実際の設定を断定するものではありません。")
st.caption("※ ぶどうは公開データ上の逆算値を利用します。実際に数えたぶどう回数ではありません。")
st.caption("※ 公開されている本八幡UNOの最新営業日集計データを取得します。画面上のデータ日付も確認してください。")
