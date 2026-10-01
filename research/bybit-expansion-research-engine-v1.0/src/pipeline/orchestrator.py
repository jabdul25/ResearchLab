from __future__ import annotations
import json
import logging
from datetime import UTC, datetime
import pandas as pd
from sqlalchemy import text
from src.collectors.bybit import BybitPublicClient, DataCollector, INTERVALS
from src.analysis.events import discover_events
from src.analysis.features import extract_features
from src.analysis.evidence import compare_winners_failures, regime_analysis, feature_importance
from src.validation.walk_forward import walk_forward
from src.strategy.candidate import CandidateSetup
from src.reports.generator import write_reports

LOG = logging.getLogger(__name__)

class ResearchOrchestrator:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings
        self.collector = DataCollector(engine, BybitPublicClient(settings.request_pause_seconds, settings.request_retries), settings.min_turnover_24h, settings.history_months)

    def _run(self):
        with self.engine.begin() as c:
            return c.execute(text("INSERT INTO research_runs(status) VALUES ('running') RETURNING id")).scalar_one()

    def _candles(self, symbol, interval):
        return pd.read_sql(text("SELECT open_time,open,high,low,close,volume,turnover FROM candles WHERE symbol=:s AND interval_minutes=:i ORDER BY open_time"), self.engine, params={"s":symbol,"i":interval})

    def collect(self) -> dict:
        symbols = self.collector.refresh_universe()
        totals={"eligible_symbols": len(symbols), "symbols_with_data": 0, "candles": 0, "oi": 0, "funding": 0}
        for n, symbol in enumerate(symbols, 1):
            try:
                saved=self.collector.collect_symbol(symbol); totals["symbols_with_data"] += 1
                for k in ("candles","oi","funding"): totals[k] += saved[k]
                LOG.info("[%d/%d] %s: %s", n, len(symbols), symbol, saved)
            except Exception:
                # One delisted/temporarily unavailable symbol must not destroy a VPS run.
                LOG.exception("collection failed for %s; continuing", symbol)
        return totals

    def detect_events(self):
        sql = text("""INSERT INTO expansion_events(symbol,interval_minutes,event_kind,start_time,peak_time,percentage_move,duration_candles,classification)
          VALUES (:symbol,:interval,:event_kind,:start_time,:peak_time,:percentage_move,:duration_candles,:classification)
          ON CONFLICT(symbol,interval_minutes,event_kind,start_time) DO UPDATE SET peak_time=EXCLUDED.peak_time,percentage_move=EXCLUDED.percentage_move,duration_candles=EXCLUDED.duration_candles,classification=EXCLUDED.classification""")
        with self.engine.connect() as c: symbols=[x[0] for x in c.execute(text("SELECT symbol FROM instruments WHERE eligible"))]
        count=0
        for symbol in symbols:
            for interval in INTERVALS:
                frame=self._candles(symbol,interval)
                records=[dict(symbol=symbol, interval=interval, **x) for x in discover_events(frame,interval)]
                if records:
                    with self.engine.begin() as c: c.execute(sql,records)
                    count += len(records)
        return count

    def build_features(self):
        events=pd.read_sql(text("SELECT id,symbol,interval_minutes,start_time FROM expansion_events ORDER BY id"),self.engine)
        upsert=text("""INSERT INTO event_features(event_id,regime,features,timeline) VALUES (:event_id,:regime,CAST(:features AS jsonb),CAST(:timeline AS jsonb))
          ON CONFLICT(event_id) DO UPDATE SET regime=EXCLUDED.regime,features=EXCLUDED.features,timeline=EXCLUDED.timeline,created_at=now()""")
        cache={}; count=0
        for event in events.itertuples(index=False):
            key=(event.symbol,event.interval_minutes)
            if key not in cache: cache[key]=self._candles(*key)
            hkey=(event.symbol,240)
            if hkey not in cache: cache[hkey]=self._candles(*hkey)
            oi=pd.read_sql(text("SELECT observed_at,open_interest FROM open_interest WHERE symbol=:s AND interval_minutes=:i ORDER BY observed_at"),self.engine,params={"s":event.symbol,"i":event.interval_minutes})
            funding=pd.read_sql(text("SELECT funding_time,funding_rate FROM funding_rates WHERE symbol=:s ORDER BY funding_time"),self.engine,params={"s":event.symbol})
            features,timeline,regime=extract_features(cache[key],cache[hkey],pd.Timestamp(event.start_time),oi,funding)
            if not features: continue
            with self.engine.begin() as c: c.execute(upsert,{"event_id":event.id,"regime":regime,"features":json.dumps(features),"timeline":json.dumps(timeline)})
            count += 1
        return count

    def analyse(self, run_id, universe):
        events=pd.read_sql(text("SELECT e.*,f.regime,f.features FROM expansion_events e JOIN event_features f ON f.event_id=e.id"),self.engine)
        if events.empty: raise RuntimeError("No featured events. Collect sufficient history before analysing.")
        values=pd.json_normalize(events.pop("features")); events=pd.concat([events,values],axis=1)
        evidence,regimes=compare_winners_failures(events),regime_analysis(events)
        importance=feature_importance(events); validation=walk_forward(events)
        candidate=CandidateSetup().generate(validation,importance)
        files=write_reports(self.settings.reports_dir,universe,events,evidence,regimes,validation,candidate)
        payloads={"evidence":evidence,"regimes":regimes,"validation":validation,"candidate":candidate}
        with self.engine.begin() as c:
            for name,payload in payloads.items(): c.execute(text("INSERT INTO research_artifacts(run_id,artifact_name,payload) VALUES (:r,:n,CAST(:p AS jsonb)) ON CONFLICT(run_id,artifact_name) DO UPDATE SET payload=EXCLUDED.payload"),{"r":run_id,"n":name,"p":json.dumps(payload)})
            c.execute(text("INSERT INTO candidate_market_states(run_id,name,conditions,evidence,validation,status) VALUES (:r,:n,CAST(:c AS jsonb),CAST(:e AS jsonb),CAST(:v AS jsonb),'RESEARCH_ONLY')"),{"r":run_id,"n":candidate["name"],"c":json.dumps(candidate["conditions"]),"e":json.dumps({"feature_importance":importance}),"v":json.dumps(validation)})
        return [str(x) for x in files], len(events)

    def run(self, collect: bool = True):
        run_id=self._run()
        try:
            universe=self.collect() if collect else {"eligible_symbols": int(pd.read_sql(text("SELECT count(*) AS n FROM instruments WHERE eligible"),self.engine).iloc[0].n), "symbols_with_data": int(pd.read_sql(text("SELECT count(DISTINCT symbol) AS n FROM candles"),self.engine).iloc[0].n)}
            detected=self.detect_events(); featured=self.build_features(); reports, total=self.analyse(run_id,universe)
            summary={**universe,"events_detected":detected,"features_built":featured,"featured_events":total,"reports":reports}
            with self.engine.begin() as c: c.execute(text("UPDATE research_runs SET status='completed',completed_at=now(),summary=CAST(:s AS jsonb) WHERE id=:r"),{"s":json.dumps(summary),"r":run_id})
            return summary
        except Exception as exc:
            with self.engine.begin() as c: c.execute(text("UPDATE research_runs SET status='failed',completed_at=now(),summary=CAST(:s AS jsonb) WHERE id=:r"),{"s":json.dumps({"error":str(exc)}),"r":run_id})
            raise
