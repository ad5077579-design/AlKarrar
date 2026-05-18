# Strategy diagrams (engineering reference)

Visual reference for contributors. Narrative: [TRADE_LOGIC.md](TRADE_LOGIC.md).

---

## 1. End-to-end tick flow

```mermaid
sequenceDiagram
  participant BF as Binance @ticker
  participant MF as mark_feed
  participant HUB as bot_hub
  participant GR as GridRunner
  participant ST as ShiftingGridStrategy
  participant BN as Binance REST

  BF->>MF: price update
  MF->>HUB: merge_room(mark)
  MF->>GR: dispatch_mark(price)
  GR->>GR: poll myTrades + realized delta
  GR->>ST: on_tick(mark, deploy, realized_delta)
  ST->>ST: boundary / shift / trailing
  ST->>ST: virtual crosses?
  alt line crossed
    ST->>BN: LIMIT IOC or MARKET
    BN-->>ST: FILLED / miss
    ST->>GR: on_exchange_fill
    GR->>HUB: WS trades_refresh
  end
```

---

## 2. Virtual grid vs resting limits

```mermaid
flowchart LR
  subgraph Traditional
    T1[LIMIT on book]
    T2[LIMIT on book]
    T3[LIMIT on book]
  end
  subgraph AlKarrar
    V1[Line in RAM]
    V2[Line in RAM]
    V3[Line in RAM]
    M[Mark cross]
    M -->|fire once| O[Single IOC order]
  end
```

---

## 3. Band shift (price breaks upper bound)

```mermaid
stateDiagram-v2
  [*] --> InBand: grid running
  InBand --> LockProfit: mark touches TP offset
  LockProfit --> Trailing: next ticks
  Trailing --> SellExit: price drops from peak
  InBand --> ShiftUp: mark > upper + lift_offset
  ShiftUp --> Rearm: GRID_SHIFT + GRID_REARM
  Rearm --> InBand: new band centered on price
  SellExit --> InBand: line idle
```

---

## 4. Trailing phases (one grid line)

```mermaid
flowchart TB
  A[idle] -->|BUY filled on exchange| B[armed for TP]
  B -->|mark >= line + trailingOffset| C[lock_profit]
  C -->|next tick| D[trailing + track peak]
  D -->|mark < peak * 1 - stop_pct| E[SELL exit]
  E --> A
```

---

## 5. Session isolation (PnL)

```mermaid
flowchart TB
  WALLET[Exchange wallet DOGE + USDT]
  subgraph Session A DOGE grid
    BUY1[BUY lot FIFO]
    BUY2[BUY lot FIFO]
    SELL1[SELL matches BUY1]
    REAL[realized_delta USDT]
  end
  WALLET -.->|only session fills| BUY1
  FLOAT[floating PnL open lots x mark]
  BUY2 --> FLOAT
```

---

## 6. Crash recovery

```mermaid
flowchart TB
  RUN[Grid running] -->|every tick| SNAP[SQLite snapshot JSON]
  RUN -->|VPS kill| DOWN[API down]
  DOWN --> UP[API startup]
  UP --> CHK{env + key fingerprint match?}
  CHK -->|no| SKIP[autoResume disabled]
  CHK -->|yes| REST[restore virtual lines + trailing]
  REST --> SYNC[REST reconcile fills]
  SYNC --> RUN
```

---

## 7. Emergency stop

```mermaid
flowchart LR
  E[Emergency button] --> S[stop GridRunner]
  S --> C[cancel open orders]
  C --> M[market sell free base]
  M --> F[freeze grid ledger]
```

---

## 8. Adding your own strategy (plugin model)

```mermaid
flowchart TB
  BS[BaseStrategy] --> IMPL[YourStrategy]
  IMPL --> GR[GridRunner optional future wiring]
  GR --> API["/grid/start today: shifting grid only"]
  NOTE[Contributions welcome: see PLUGINS.md]
```
