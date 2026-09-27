<!-- Migrated from Google Docs on 2026-09-27. Historical source document ID: 1h7Z-MgO0BZ7dORNUVsB0TU6IXt36N8XJOKxdkuCCEX4. Source revision at migration: ANLCKQlxQ5D96152v1RpZxugo-kVdlBIhZyURgJ6MuoKcFJEt3_mRGk7hpQ_G7xz17jTGb_wfucbaSl5mkftcevkHWbrh2NxLQmqOT9Ox1o. -->

# Product Scope — Intraday Market Analysis & Research Platform

## Purpose

- Build a deterministic digital environment for intraday market analysis, pattern research, historical replay, quantitative evaluation, and live interpretation.

- Bridge discretionary chart reading and systematic testing by allowing manually defined market-behaviour formulas to be replayed visually, evaluated autonomously, and reused in live analysis.

- Include EMA-45 trend-leg detection as one market-state primitive rather than making it the sole representation of direction.

## Product Positioning

- The product is primarily a market-behaviour research and interaction platform, not an automated trading system.

- The defining workflow is: observe → formalize → replay → inspect → batch-test → compare → reuse live.

- The platform should explain why a detector fired and preserve reproducibility through versioned formulas and configurations.

## Primary Users

- Discretionary intraday trader: studies structure, momentum, reversal, continuation, range behaviour, and session context.

- Research-oriented trader / quant developer: defines deterministic pattern logic and evaluates historical outcomes.

- System developer: validates consistency between historical replay and live processing.

- Future analyst/reviewer: compares pattern versions and outcome distributions across instruments, sessions, and regimes.

## Core Use Cases

- Load historical OHLC data for a selected instrument, timeframe, and date/time range.

- Replay candles progressively with play, pause, step, speed, and event-jump controls.

- Compute market-state primitives such as EMA, ATR, trend legs, swings, range state, support/resistance, volatility, and session context.

- Evaluate manually defined patterns such as reversal, compression, continuation, rejection, breakout, exhaustion, and liquidity events.

- Display detector state changes directly on the chart as the replay progresses.

- Run the same detectors autonomously over longer historical periods without visual replay.

- Record event occurrences and evaluate forward outcomes such as MFE, MAE, forward returns, structure breaks, and subsequent state transitions.

- Compare detector versions and parameters.

- Reuse the same analysis runtime against live data.

- Optionally generate alerts from confirmed live pattern events at a later stage.

## Operating Modes

- Historical replay: deterministic bar-by-bar simulation with visual chart interaction.

- Batch research: fast autonomous processing of historical periods for event and outcome statistics.

- Live analysis: process incoming bars through the same market-state and detector runtime.

- Strategy simulation: optional later layer that converts selected pattern events into explicit entry, exit, stop, and position-management rules.

## In-Scope Analytical Concepts

- Raw OHLC/volume data and session calendars.

- EMA-45 trend legs and associated duration, magnitude, span, and efficiency.

- Swing structure: HH, HL, LH, LL and structure breaks.

- Range and compression state.

- Expansion, rejection, breakout, continuation, reversal, and exhaustion patterns.

- Support/resistance zones and proximity/context.

- Volatility context using ATR or equivalent measures.

- Session/time-of-day context.

- Event-time and detection-time separation to prevent hindsight leakage.

## Product Principles

- One analytical runtime across replay, batch, and live modes.

- Deterministic and explainable logic before predictive or ML logic.

- Pattern detectors are stateful: candidate, forming, confirmed, active, completed, or invalidated as appropriate.

- Detection is separate from outcome evaluation.

- Outcome evaluation is separate from trading-strategy simulation.

- Every detector event is traceable to a pattern definition, version, parameters, and source data.

- No detector may use future information before it becomes observable.

- Visual validation and statistical validation must originate from the same underlying event stream.

- Continuous internal scores are preferred where useful; user-facing labels may simplify them.

## Non-Goals for the Initial Product

- Live order execution.

- Brokerage integration for real-money trading.

- Predictive machine-learning models.

- Automated optimization or genetic parameter search.

- Broad multi-asset scale across large instrument universes.

- Full portfolio management.

- Multi-day position management.

- Attempting to formalize every discretionary chart concept.

- Production-grade distributed microservice infrastructure.

## Initial Instrument and Timeframe Focus

- Primary focus: US30 and DAX.

- Initial analytical timeframe: 1-minute OHLC.

- Multi-timeframe context may be derived later from the same underlying data.

- Data-provider integration must remain replaceable; OANDA may be used initially where appropriate, but the domain model must not depend on one provider.

## Success Criteria

- Historical replay is deterministic and reproducible.

- Replay and captured live processing produce identical detector results for the same ordered bar sequence.

- No known look-ahead leakage in detector logic.

- A user can define or modify a detector, visually inspect it, batch-test it, and compare versions without rewriting the surrounding system.

- Pattern events include sufficient rationale to explain why they were emitted.

- Initial detectors produce stable, reviewable event datasets across meaningful historical samples.

- Core research workflow is useful before live alerting is added.

## Major Risks and Assumptions

- Look-ahead bias in swings, pivots, reversals, and post-event labels.

- Overfitting caused by many parameter combinations and conditional segments.

- Ambiguous discretionary concepts that cannot be reduced to measurable conditions.

- Market-data licensing, history availability, gaps, and provider-specific conventions.

- Scope expansion into alerting, execution, ML, and infrastructure before the research loop is proven.

- Pattern definitions may behave differently across instruments and volatility regimes; evaluation must preserve this contex.t

Approved MVP product reconciliation — 2026-09-24

- MVP delivery is a local single-user research/replay application for the researcher's Mac; cloud hosting, authentication, remote/LAN access and always-on operation are post-MVP.

- Historical OANDA import is the only MVP feature that requires network access. Once imported, immutable local dataset revisions support offline replay and evaluation.

- The browser supports schema-driven parameter editing, immutable named preset revisions, live walkthrough/replay, queued autonomous evaluations, results review and comparison.

- Multiple evaluation runs may be scheduled from the UI. They execute sequentially, exactly one at a time; parallel autonomous evaluation is post-MVP.

- Dataset revisions are immutable and reproducible. Normalized one-minute bars are retained in versioned Parquet with provenance/checksums; analytical metadata, configurations, jobs, events, outcomes and annotations are persisted transactionally.

- The same deterministic completed-bar analytical engine is used by interactive walkthrough and autonomous evaluation. Future live feeds remain post-MVP but must later enter through the same canonical normalization/validation boundary.

- The approved TrendLeg concept is structural and protected-swing based. EMA-cross segments remain useful detector evidence but are not themselves TrendLeg boundaries.

- MVP operational support includes one Docker Compose startup, local diagnostics, bounded logs, manual backup/restore, seeded non-research demo data and lineage-aware export.

Repo reconciliation check — 2026-09-26

- The provisional research defaults added to the repository after the 2026-09-24 reconciliation refine configuration, warm-up and TrendLeg-evidence contracts without changing the Product Scope boundary.

- Exact provider-calendar values, viewport aggregation limits and benchmark pinning remain implementation-time decisions rather than additional product scope.
