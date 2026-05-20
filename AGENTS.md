# AlKarrar Pro — دليل المشروع للوكيل (اقرأ هذا أولاً)

> **Human contributors:** start with [README.md](README.md), [ARCHITECTURE.md](ARCHITECTURE.md), [docs/TRADE_LOGIC.md](docs/TRADE_LOGIC.md), [CONTRIBUTING.md](CONTRIBUTING.md).



مشروع تداول **Binance Spot** مع لوحة تحكم **Nuxt 3 + Pinia** وخادم **FastAPI** (BFF + WebSocket). الافتراضي الموصى به في `.env.example`: **Spot Demo** (`BINANCE_ENV=demo`) — مفاتيح من [demo.binance.com](https://demo.binance.com).



---



## تشغيل سريع (Windows)



**أمر واحد فقط (موصى به):** من جذر المستودع:

```powershell
.\start.ps1
```

يفرّغ المنافذ **8090** و**3000**، يفتح نافذتين (خلفية API + واجهة Nuxt)، ويفتح المتصفح على http://localhost:3000.  
**إيقاف:** `.\stop.ps1` — أو `.\restart_all.ps1` / `.\start.ps1` (نفس التشغيل).

```powershell
.\start.ps1 -NoApiReload   # بدون uvicorn --reload
```

(للمطورين فقط — يدوياً: `.\scripts\run_api.ps1` + `.\scripts\run_frontend.ps1` — **لا حاجة** إذا استخدمت `.\start.ps1`.)



- انسخ `.env.example` → `.env` وضع المفاتيح و**طابق** `BINANCE_ENV` مع مصدر المفتاح (انظر الجدول أدناه).

- **لا تلصق المفاتيح في الدردشة أو Git.**



اختبار المفاتيح:



```powershell

python scripts/test_binance_spot_keys.py

python scripts/test_binance_spot_keys.py --env demo

python scripts/test_binance_spot_keys.py --env testnet

python scripts/test_binance_spot_keys.py --mainnet

```



إذا فشل `import requests` / `simplejson`: `pip install -r requirements.txt`.



---



## هيكل المجلدات



| المسار | الدور |

|--------|--------|

| `backend/api/` | FastAPI، `bot_hub`، WS `/ws`، `spot_account_sync`، `spot_user_stream`، `mark_feed` |

| `backend/core/binance_env.py` | توجيه البيئات: `mainnet` \| `testnet` \| `demo` (REST + WS hosts) |

| `backend/core/binance_client.py` | عميل Spot (`BinanceSpotClient`: REST + user stream URL) |

| `backend/api/credential_resolver.py` | مفاتيح `.env` + اكتشاف env تلقائي → `(key, secret, env, legacy)` |

| `backend/api/binance_pool.py` | عميل REST مشترك لكل مزامنة (حسب env) |

| `backend/database/` | SQLite `data/alkarrar.db` (إعدادات، مفاتيح، orders، trade_fills) |

| `backend/strategies/` | شبكة متحركة (`alkarrar_pro_shifting_grid`) |

| `backend/main_engine.py` | `EngineSettings` من `.env` + `resolved_binance_env()` |

| `frontend/` | Nuxt 3، `pages/index.vue`، `stores/bot.ts`، `TradingChart.vue` |

| `scripts/` | `run_api.ps1`, `run_frontend.ps1`, `test_binance_spot_keys.py` |



---



## بيئات Binance Spot (مهم جداً)



### المتغير `BINANCE_ENV`



قيم **حصرية** في `.env`:



| `BINANCE_ENV` | أنشئ المفاتيح من | REST API | WebSocket (ticker / user) |

|---------------|-------------------|----------|---------------------------|

| **`demo`** (افتراضي في `.env.example`) | [demo.binance.com](https://demo.binance.com) → API Management | `https://demo-api.binance.com/api` | `wss://demo-stream.binance.com:9443` |

| **`testnet`** | [testnet.binance.vision](https://testnet.binance.vision) | `https://testnet.binance.vision/api` | `wss://testnet.binance.vision` |

| **`mainnet`** | binance.com → API Management | `https://api.binance.com/api` | `wss://stream.binance.com:9443` |



- إذا **لم** يُعرَّف `BINANCE_ENV`: يُستنتج من `BINANCE_TESTNET` — `true` → **testnet**، `false` → **mainnet**.

- عميل python-binance: `AsyncClient(testnet=..., demo=...)` — لا تخلط `testnet=true` مع مفاتيح Demo (يسبب **-2015**).



### التوجيه في الكود



1. `EngineSettings.resolved_binance_env()` في `backend/main_engine.py`

2. `normalize_binance_env()` / `spot_stream_endpoint()` في `backend/core/binance_env.py`

3. `BinanceSpotClient.create_for_env(env=...)` في `backend/core/binance_client.py`

4. `get_binance_keys(bot_id)` → `(key, secret, env, legacy)` في `credential_resolver.py`

   - مفاتيح **قاعدة البيانات** مع `binanceTestnet=false` → **mainnet** دائماً

   - مع `binanceTestnet=true` → `env` من إعدادات السيرفر (`.env` / `BINANCE_ENV`)



### حقول لوحة التحكم (API + WS)



| الحقل | المعنى |

|--------|--------|

| `binanceTestnet` | `true` لـ testnet **أو** demo (أي غير mainnet) — عقد قديم، لا تُعاد تسميته |

| `binanceEnv` | `mainnet` \| `testnet` \| `demo` — البيئة الفعلية للـ REST/WS |

| `exchangeTestnet` | نفس منطق `binanceTestnet` (غير mainnet) |



---



## الواجهة الأمامية (Nuxt)



- **تطوير:** `apiBase` و `wsUrl` فارغان → الطلبات نسبية `/api/...` و `/ws` عبر **proxy** إلى `127.0.0.1:8090`.

- **WebSocket:** المتصفح يتصل بـ `ws://localhost:3000/ws` (مُوجّه للـ API).

- **إنتاج:** عيّن `NUXT_PUBLIC_API_BASE` و/أو `NUXT_PUBLIC_WS_URL` إن لزم.



### Pinia (`frontend/stores/bot.ts`)



- `connectWs()` → رسائل: `snapshot`, `mark`, `metrics`, `settings`, `sync_error`, `order`, `emergency`.

- أرصدة حية: `currentCapital`, `availableBalance`, `totalWalletBalance`, `binanceEnv`, `exchangeTestnet`, `syncError`.



### الرسم (`frontend/components/TradingChart.vue`)



- شموع: `GET /api/bots/{botId}/klines?interval=15m&limit=200` (نفس `env` كالمفاتيح)

- خط Mark من بث WS `mark_feed`



---



## API (FastAPI) — مسارات رئيسية



| Method | Path | الوظيفة |

|--------|------|---------|

| GET | `/api/bots/{bot_id}/dashboard` | لقطة + مزامنة REST للحساب |

| GET | `/api/bots/{bot_id}/klines` | شموع Spot (حسب `env`) |

| GET | `/api/bots/{bot_id}/markets` | أزواج USDT |

| GET | `/api/bots/{bot_id}/trades` | سجل صفقات |

| PATCH | `/api/bots/{bot_id}/settings` | `generatorUpper`, `generatorLower`, `generatorCount`, `initialCapital` |

| GET/POST/DELETE | `/api/bots/{bot_id}/credentials` | `binanceApiKey`, `binanceApiSecret`, `binanceTestnet` |

| POST | `/api/bots/{bot_id}/grid/start` \| `stop` | تشغيل/إيقاف الشبكة |

| POST | `/api/emergency_stop` | إلغاء أوامر + بيع base (Spot) |

| WS | `/ws` | بث حالة البوت |



---



## مهام خلفية عند تشغيل API



1. **`spot_account_sync`** — REST دوري (`ALKARRAR_ACCOUNT_SYNC`): رصيد USDT + ticker → `metrics` / `mark`.

2. **`spot_user_stream`** — listenKey + `executionReport` (`ALKARRAR_USER_STREAM`). على **demo** قد يفشل `userDataStream` (410) — الرصيد يبقى عبر REST.

3. **`mark_feed`** — `@ticker` عام (`ALKARRAR_MARK_FEED`) على host الـ `env` الحالي.



---



## عقود التسمية (لا تكسرها)



`generatorUpper`, `generatorLower`, `generatorCount`, `initialCapital`, `binanceApiKey`, `binanceApiSecret`, `binanceTestnet`, `bot_id` = `default`.



حقول إضافية للعرض (ليست عقد إدخال من الواجهة): `binanceEnv`.



---



## استكشاف الأخطاء



| العرض | السبب المحتمل |

|--------|----------------|

| رصيد 0 مع مفاتيح صحيحة | `BINANCE_ENV` لا يطابق مصدر المفتاح؛ أو محفظة فارغة |

| **`-2015`** / Invalid API-key | مثلاً مفاتيح **demo.binance.com** مع `BINANCE_ENV=testnet` (أو العكس) |

| `listenKey` / user stream فاشل على demo | Spot Demo قد لا يدعم `POST /api/v3/userDataStream` (410) — استخدم مزامنة REST |

| `Failed to fetch` | API متوقف أو proxy Nuxt |

| WS offline | API متوقف أو `NUXT_PUBLIC_WS_URL` خاطئ |

| مفاتيح DB تتفوق على `.env` | احذف credentials من اللوحة أو عيّن `binanceTestnet` + أعد تشغيل API |



**مثال `.env` لمفاتيح Spot Demo:**



```env

BINANCE_ENV=demo

BINANCE_API_KEY=...

BINANCE_API_SECRET=...

BINANCE_TESTNET=true

```



---



## Git / أمان



- لا تُ commit ملف `.env` أو مفاتيح API.

- لا تُنشئ commits إلا إذا طلب المستخدم صراحة.

