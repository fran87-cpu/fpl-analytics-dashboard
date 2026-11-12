import requests
import pandas as pd
import streamlit as st
import plotly.express as px
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(page_title="FPL Analytics v1", page_icon="⚽", layout="wide")
BASE = "https://fantasy.premierleague.com/api"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent":"Mozilla/5.0 (compatible; FPLAnalytics/1.0)","Accept":"application/json"})

@st.cache_data(ttl=900, show_spinner=False)
def api(path):
    url = f"{BASE}/{path.lstrip('/')}"
    r = SESSION.get(url, timeout=25)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=3600, show_spinner=False)
def history(player_id):
    return api(f"element-summary/{int(player_id)}/")

def numeric(frame, cols):
    for c in cols:
        if c in frame.columns:
            frame[c] = pd.to_numeric(frame[c], errors="coerce")
    return frame

def display_table(df, preferred, key):
    cols = [c for c in preferred if c in df.columns]
    if not cols:
        st.info("No statistics available for this selection.")
        return
    st.dataframe(df[cols], hide_index=True, use_container_width=True, key=key)
    st.download_button("Download CSV", df[cols].to_csv(index=False).encode(), file_name=f"fpl_{key}.csv", mime="text/csv", key=f"csv_{key}")

st.title("⚽ FPL Analytics Dashboard")
st.caption("Version 1 · Official FPL public data · Performance tracking + transfer analysis")
with st.sidebar:
    st.header("Data controls")
    if st.button("Refresh FPL data"):
        api.clear()
        history.clear()
        st.rerun()
    st.caption("Data cached for 15 minutes. Player histories cached for 1 hour.")

try:
    bootstrap = api("bootstrap-static/")
    fixtures = api("fixtures/")
except Exception as exc:
    st.error(f"Could not reach the official FPL API from this server: {exc}")
    st.info("If deployed on Streamlit Cloud, the FPL service may be blocking or rate-limiting the cloud IP. Try Refresh later. This dashboard does not use an unreliable public CORS proxy.")
    st.stop()

players = pd.DataFrame(bootstrap.get("elements", []))
teams = pd.DataFrame(bootstrap.get("teams", []))
positions = pd.DataFrame(bootstrap.get("element_types", []))
if players.empty:
    st.error("The FPL API returned no players.")
    st.stop()
team_names = dict(zip(teams.get("id", []), teams.get("short_name", [])))
team_long = dict(zip(teams.get("id", []), teams.get("name", [])))
position_names = dict(zip(positions.get("id", []), positions.get("singular_name_short", [])))
players["Player"] = players.get("web_name", players.get("second_name", ""))
players["Team"] = players["team"].map(team_names)
players["Position"] = players["element_type"].map(position_names)
players["Price (£m)"] = pd.to_numeric(players.get("now_cost"), errors="coerce") / 10
players["FPL profile"] = players["id"].map(lambda x: f"https://fantasy.premierleague.com/en/players/{x}")
players = numeric(players, ["total_points","minutes","goals_scored","assists","bonus","bps","clean_sheets","expected_goals","expected_assists","expected_goal_involvements","form","points_per_game","selected_by_percent","defensive_contribution","defensive_contributions"])
players["Points / £m"] = players["total_points"] / players["Price (£m)"].replace(0, float("nan"))
players["Points / 90"] = players["total_points"] * 90 / players["minutes"].replace(0, float("nan"))
current_events = [e for e in bootstrap.get("events", []) if e.get("is_current")]
finished_events = [e for e in bootstrap.get("events", []) if e.get("finished")]
default_gw = (current_events[0]["id"] if current_events else max((e["id"] for e in finished_events), default=1))
st.caption(f"{len(players)} players · {len(teams)} clubs · Current/reference GW: {default_gw}")

tabs = st.tabs(["📊 Player Rankings", "🏟️ Gameweek Box Scores", "📈 Player History", "🎯 Transfer Radar", "📅 Fixtures"])
with tabs[0]:
    st.subheader("Season player rankings")
    c1,c2,c3 = st.columns(3)
    pos = c1.multiselect("Position", sorted(players["Position"].dropna().unique()), key="rank_pos")
    club = c2.multiselect("Club", sorted(players["Team"].dropna().unique()), key="rank_club")
    mins = c3.number_input("Minimum minutes", min_value=0, value=0, step=90)
    view = players.copy()
    if pos: view = view[view["Position"].isin(pos)]
    if club: view = view[view["Team"].isin(club)]
    view = view[view["minutes"].fillna(0) >= mins]
    options = [x for x in ["total_points","form","points_per_game","Points / 90","Points / £m","bonus","bps","goals_scored","assists","minutes","expected_goal_involvements","defensive_contribution","defensive_contributions"] if x in view.columns]
    metric = st.selectbox("Rank by", options)
    ascending = st.checkbox("Ascending", value=False)
    view = view.sort_values(metric, ascending=ascending, na_position="last")
    display_table(view, ["Player","Team","Position","Price (£m)","total_points","form","points_per_game","Points / 90","Points / £m","minutes","goals_scored","assists","bonus","bps","clean_sheets","expected_goals","expected_assists","expected_goal_involvements","defensive_contribution","defensive_contributions","selected_by_percent","FPL profile"], "rankings")

with tabs[1]:
    st.subheader("Gameweek box scores")
    event_ids = [e["id"] for e in bootstrap.get("events", [])]
    gw = st.selectbox("Gameweek", event_ids, index=event_ids.index(default_gw) if default_gw in event_ids else 0)
    gw_fixtures = [f for f in fixtures if f.get("event") == gw]
    fixture_options = {f"{team_long.get(f.get('team_h'),'Home')} vs {team_long.get(f.get('team_a'),'Away')} · {str(f.get('kickoff_time',''))[:10]}": f["id"] for f in gw_fixtures}
    fixture_name = st.selectbox("Match (optional)", ["All fixtures"] + list(fixture_options))
    try:
        live = api(f"event/{gw}/live/")
        records = []
        for e in live.get("elements", []):
            p = players.loc[players["id"] == e.get("id")]
            if p.empty: continue
            p = p.iloc[0]
            for ex in (e.get("explain") or []):
                fixture_id = ex.get("fixture")
                if fixture_name != "All fixtures" and fixture_id != fixture_options[fixture_name]: continue
                row = {"Player":p["Player"], "Team":p["Team"], "Position":p["Position"], "Fixture ID":fixture_id}
                for stat in ex.get("stats", []):
                    name = stat.get("identifier")
                    if name: row[name] = stat.get("value", 0)
                    row["FPL points"] = row.get("FPL points",0) + stat.get("points",0)
                records.append(row)
        if records:
            frame = pd.DataFrame(records).fillna(0)
            if "FPL points" in frame.columns: frame = frame.sort_values("FPL points",ascending=False)
            display_table(frame, ["Player","Team","Position","Fixture ID","FPL points","minutes","goals_scored","assists","clean_sheets","bonus","bps","defensive_contribution","defensive_contributions","saves","yellow_cards","red_cards"], "boxscores")
        else:
            st.info("No fixture-level statistics returned for this selection yet. Upcoming matches will populate as FPL publishes live data.")
    except Exception as exc:
        st.warning(f"Gameweek live endpoint unavailable: {exc}")

with tabs[2]:
    st.subheader("Player match history")
    labels = {f"{p.Player} · {p.Team} · {p.Position} · ID {p.id}": int(p.id) for p in players.itertuples()}
    chosen = st.selectbox("Player", sorted(labels))
    try:
        h = pd.DataFrame(history(labels[chosen]).get("history", []))
        if h.empty:
            st.info("No match history available.")
        else:
            h = numeric(h,["total_points","minutes","goals_scored","assists","bonus","bps","expected_goals","expected_assists","defensive_contribution","defensive_contributions"])
            h = h.sort_values([c for c in ["kickoff_time","fixture"] if c in h.columns])
            c1,c2,c3,c4 = st.columns(4)
            for col,n in zip([c1,c2,c3],[3,5,10]):
                col.metric(f"Last {n} matches avg points",f"{h['total_points'].tail(n).mean():.2f}")
            c4.metric("Matches",len(h))
            if "kickoff_time" in h:
                fig = px.line(h, x="kickoff_time", y="total_points", markers=True, title="FPL points by match")
                st.plotly_chart(fig, use_container_width=True)
            display_table(h.iloc[::-1], ["round","kickoff_time","opponent_team","was_home","minutes","total_points","goals_scored","assists","bonus","bps","defensive_contribution","defensive_contributions","expected_goals","expected_assists"], "history")
    except Exception as exc:
        st.warning(f"Player history unavailable: {exc}")

with tabs[3]:
    st.subheader("Transfer radar")
    st.caption("Transparent heuristic, not a projection: 30% form, 25% points/90, 20% xGI/90, 15% upcoming fixtures, 10% value. Ranks relative to currently filtered players.")
    target = players.copy()
    min_play = st.slider("Minimum season minutes", 0, 2000, 450, 90)
    max_price = st.slider("Maximum price (£m)", 3.0, 16.0, 10.0, 0.5)
    target = target[(target["minutes"].fillna(0)>=min_play) & (target["Price (£m)"]<=max_price)].copy()
    upcoming = [f for f in fixtures if f.get("event") and f["event"] >= default_gw and not f.get("finished")]
    upcoming.sort(key=lambda f: (f.get("event",99), f.get("kickoff_time") or ""))
    difficulty = {}
    for team_id in team_names:
        vals=[]
        for f in upcoming:
            if f.get("team_h") == team_id: vals.append(f.get("team_h_difficulty"))
            elif f.get("team_a") == team_id: vals.append(f.get("team_a_difficulty"))
            if len(vals)>=5: break
        vals=[float(v) for v in vals if v is not None]
        difficulty[team_id]=sum(vals)/len(vals) if vals else None
    target["Next 5 FDR"] = target["team"].map(difficulty)
    target["xGI / 90"] = pd.to_numeric(target.get("expected_goal_involvements",pd.Series(0,index=target.index)),errors="coerce")*90/target["minutes"].replace(0,float("nan"))
    def percentile(series):
        return pd.to_numeric(series,errors="coerce").rank(pct=True).fillna(0.5)*100
    if not target.empty:
        form = percentile(target["form"])
        p90 = percentile(target["Points / 90"])
        xgi = percentile(target["xGI / 90"])
        fdr = percentile(-target["Next 5 FDR"])
        value = percentile(target["Points / £m"])
        target["Radar score"] = (0.30*form+0.25*p90+0.20*xgi+0.15*fdr+0.10*value).round(1)
        target = target.sort_values("Radar score",ascending=False)
        display_table(target,["Player","Team","Position","Price (£m)","Radar score","total_points","form","Points / 90","xGI / 90","Next 5 FDR","Points / £m","minutes","selected_by_percent"],"radar")
    else:
        st.info("No players match these filters.")

with tabs[4]:
    st.subheader("Fixtures and difficulty")
    fixture_rows=[]
    for f in fixtures:
        fixture_rows.append({"GW":f.get("event"),"Kickoff":f.get("kickoff_time"),"Home":team_long.get(f.get("team_h")),"Away":team_long.get(f.get("team_a")),"Home FDR":f.get("team_h_difficulty"),"Away FDR":f.get("team_a_difficulty"),"Home goals":f.get("team_h_score"),"Away goals":f.get("team_a_score"),"Finished":f.get("finished")})
    ff = pd.DataFrame(fixture_rows)
    if not ff.empty:
        only_future = st.checkbox("Only upcoming fixtures", value=True)
        if only_future: ff=ff[ff["Finished"]!=True]
        display_table(ff,["GW","Kickoff","Home","Away","Home FDR","Away FDR","Home goals","Away goals","Finished"],"fixtures")

st.divider()
st.caption("Unofficial analytics tool using public Fantasy Premier League data. Not affiliated with the Premier League. API availability and scoring fields may change.")
