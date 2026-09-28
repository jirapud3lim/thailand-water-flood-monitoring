# Implementation Plan: ย้าย Thailand Water Flood Monitoring ไป ChatGPT Sites

> **สถานะ:** วางแผนแล้ว ยังไม่เริ่มพัฒนา
>
> **แนวทางที่เลือก:** ย้ายทั้ง frontend และ backend ไปอยู่ใน ChatGPT Sites โดยคง Python/FastAPI ไว้เป็นระบบอ้างอิงระหว่างการย้าย จนกว่า TypeScript implementation จะผ่าน contract tests และทดสอบกับ upstream จริง
>
> **เหตุผล:** frontend ปัจจุบันเป็น HTML/CSS/JavaScript และย้ายได้ตรงไปตรงมา แต่ ChatGPT Sites รองรับ full-stack JavaScript/TypeScript ไม่ได้ระบุว่ารัน Python/FastAPI ได้โดยตรง

## 1. เป้าหมาย

1. Deploy dashboard เป็น ChatGPT Site จาก local project ได้
2. คง URL contract เดิมของ `/api/v1/*` เพื่อให้ frontend เปลี่ยนน้อยที่สุด
3. คงความหมายข้อมูล เกณฑ์เตือน freshness metadata, ETag, stale fallback และ error behavior เดิม
4. เก็บ API keys ใน hosted environment settings เท่านั้น
5. ทำให้ cache และ rate limit ใช้ได้เมื่อ runtime มีหลาย instance หรือเกิด cold start
6. เปิด Site แบบ private ก่อน แล้วจึงเผยแพร่หลังผ่านข้อมูลจริง ความปลอดภัย และ load test

## 2. ขอบเขต

### อยู่ในขอบเขต

- สร้าง Sites-compatible JavaScript/TypeScript project จาก official Sites starter
- ย้าย FastAPI routes และ service adapters เป็น TypeScript
- ใช้ D1 สำหรับ JSON cache, source health, quota และ distributed rate-limit state
- ใช้ R2 สำหรับ binary tile cache ที่ต้องแชร์ระหว่าง requests/instances
- ย้าย frontend เดิมโดยรักษาหน้าตาและ interaction หลัก
- เพิ่ม contract, integration, browser และ load tests
- สร้าง private deployment, ตรวจ production URL และเตรียม public rollout

### ไม่อยู่ในขอบเขต

- เปลี่ยน UX ครั้งใหญ่
- เปลี่ยนเกณฑ์อุทกวิทยาหรือความหมายของ severity
- เพิ่มแหล่งข้อมูลใหม่
- เปลี่ยนผู้ให้บริการ upstream
- ลบ Python/FastAPI ก่อน TypeScript version ผ่านเกณฑ์รับงานทั้งหมด
- เปิด public access โดยอัตโนมัติ

## 3. ข้อจำกัดที่กำหนดสถาปัตยกรรม

| หัวข้อ | สภาพปัจจุบัน | ผลต่อ Sites |
|---|---|---|
| Runtime | Python, FastAPI, Uvicorn | ต้องย้าย server code เป็น JavaScript/TypeScript |
| Frontend | HTML, CSS, JavaScript, Leaflet | ใช้ต่อได้ โดยคง same-origin `/api/v1/*` |
| JSON cache | `AsyncTTLCache` ใน process | ห้ามพึ่ง RAM เป็น shared cache; ใช้ D1 |
| Radar tile cache | LRU ใน RAM สูงสุด 96 MB | ใช้ R2 เป็น shared binary cache และมี eviction policy |
| Rate limit | lock และ sliding window ใน process | ย้าย state ไป D1 เพื่อคุมรวมทุก instance |
| Secrets | `.env` ผ่าน `pydantic-settings` | ตั้งค่าใน Site settings; ห้ามใส่ค่า secret ใน repo หรือ `.openai/hosting.json` |
| Upstream | HTTP/HTTPS หลายหน่วยงาน | Sites รองรับ HTTP/HTTPS แต่ต้องทำ connectivity spike ก่อนย้ายทั้งหมด |
| Scheduled work | ไม่มี background worker; browser poll แล้ว API fetch on demand | คง request-driven design; ไม่เพิ่ม cron/background service |
| Payload | JSON ใหญ่และ binary tiles | ตรวจ response compression, size limit, timeout และ cache headers บน runtime จริง |

## 4. สถาปัตยกรรมเป้าหมาย

```text
Browser / Leaflet
  ├─ static HTML, CSS, JavaScript
  └─ /api/v1/*
       ├─ route handlers (TypeScript)
       ├─ validation + response contract
       ├─ source adapters
       ├─ D1: JSON cache, health, quota, distributed limiter
       ├─ R2: radar/GISTDA/terrain/traffic tile cache
       └─ external HTTP/HTTPS upstreams
```

หลักการ:

- คง endpoint path, query parameter, status code และ response shape เดิม
- แยก route, source adapter, normalization และ cache เหมือนโครง Python เดิม
- ใช้ relative URL ใน frontend จึงไม่ต้องเพิ่ม CORS
- ใช้ immutable cache เฉพาะข้อมูลที่ไม่เปลี่ยน เช่น radar frame เก่าและ terrain tile
- เก็บ stale copy พร้อม `fetched_at`, `expires_at`, `version` และ health counters
- ไม่ใช้ D1/R2 เป็นแหล่งข้อมูลหลักทางอุทกวิทยา ทั้งสองเป็น cache เท่านั้น

## 5. โครงไฟล์เป้าหมาย

ชื่อ directory จริงให้ยึด official starter ที่ Sites สร้างให้ ห้ามฝืน framework convention ของ starter แผนนี้ใช้ชื่อเชิงตรรกะดังนี้:

```text
site/
  public/
    index.html
    app.js
    styles.css
    rules.js
    country-clip.js
    terrain.js
    thailand-boundary.js
  src/
    routes/api/v1/...
    services/
      http.ts
      cache.ts
      freshness.ts
      upstreams.ts
      thaiwater.ts
      gistda.ts
      tmd.ts
      radar-tiles.ts
      terrain-tiles.ts
      traffic.ts
      local-news.ts
      local-social.ts
      dam-photos.ts
      river-flow.ts
      twins.ts
    data/
      provinces.json
      administrative_areas.json
      dam_photos.json
    types/
      api.ts
      bindings.ts
  migrations/
  tests/
    contract/
    integration/
    browser/
.openai/hosting.json
```

`.openai/hosting.json` ให้ Sites สร้างและผูก `project_id`, D1 และ R2 bindings ห้ามเขียน `project_id` หรือ secret ขึ้นเองล่วงหน้า

## 6. Phase 0: Compatibility spike และ baseline

### 0.1 บันทึก baseline ของระบบ Python

- รัน test suite ปัจจุบันและเก็บผล
- เก็บตัวอย่าง response จากทุก endpoint โดยปิดบังคับค่าที่เปลี่ยนตามเวลา
- บันทึก status code, headers, response schema, payload size และเวลาตอบ
- เตรียม fixture ที่ไม่มี secret และไม่มีข้อมูลส่วนบุคคล
- บันทึกจำนวน upstream calls ต่อ user flow หลัก

### 0.2 สร้าง private Sites starter

- เปิด Sites ผ่าน ChatGPT web หรือ desktop app
- สร้าง project จาก local source โดยยังไม่ publish public
- ให้ Sites สร้าง starter, build scripts และ `.openai/hosting.json`
- เปิด D1 และ R2 bindings ด้วยชื่อที่ starter/runtime รองรับ
- ยืนยันว่า save version สำเร็จก่อน deploy

### 0.3 ทำ vertical slice ขั้นต่ำ

ย้ายเพียง:

1. static homepage
2. `GET /api/v1/health`
3. `GET /api/v1/weather/current`
4. D1 cache หนึ่ง key พร้อม stale fallback

ตรวจ:

- outbound HTTPS ไป Open-Meteo
- timeout และ retry
- response compression และ headers
- D1 read/write จาก production runtime
- cold start และ concurrent requests
- log ไม่มี query secret หรือ upstream URL ที่มี secret

**เกณฑ์ผ่าน:** private deployment เปิดได้, health ตอบ `200`, weather contract ตรง Python และ stale fallback ทำงานหลังจำลอง upstream failure

**Stop condition:** หาก runtime จำกัด outbound host, response size, binary response หรือ execution time จน vertical slice ใช้งานไม่ได้ ให้เปลี่ยนเป็น hybrid deployment: frontend บน Sites และ FastAPI บน external host

## 7. Phase 1: Scaffold และ shared platform layer

### 1.1 TypeScript foundation

- เปิด strict TypeScript
- กำหนด binding types สำหรับ D1, R2 และ environment values
- สร้าง request validation สำหรับ path/query parameters
- สร้าง error mapper ให้ status code และข้อความสอดคล้องกับ FastAPI
- กำหนด Bangkok timezone utilities และ ISO timestamp format เดิม

### 1.2 HTTP client

ย้าย behavior จาก `backend/app/services/http.py`:

- timeout เริ่มต้น 12 วินาที
- retry เฉพาะ timeout, transport error, `429`, `500`, `502`, `503`, `504`
- ใช้ `Retry-After` เมื่อปลอดภัยและจำกัดเวลารอ
- ไม่ retry `4xx` อื่น
- แยก JSON และ binary fetch
- redact secrets จาก error และ logs
- ตั้ง `User-Agent` และ `Accept` ตามชนิด response

### 1.3 D1 cache

สร้าง schema ขั้นต่ำ:

```sql
CREATE TABLE response_cache (
  cache_key TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  version TEXT NOT NULL,
  last_accessed_at INTEGER NOT NULL
);

CREATE TABLE source_health (
  source_key TEXT PRIMARY KEY,
  last_success_at TEXT,
  last_error_at TEXT,
  last_error TEXT,
  hits INTEGER NOT NULL DEFAULT 0,
  misses INTEGER NOT NULL DEFAULT 0,
  stale_served INTEGER NOT NULL DEFAULT 0,
  upstream_errors INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE rate_limit_bucket (
  bucket_key TEXT PRIMARY KEY,
  window_started_at INTEGER NOT NULL,
  used INTEGER NOT NULL
);
```

ข้อกำหนด:

- fresh hit ไม่ยิง upstream
- concurrent miss ของ key เดียวกันต้องลด duplicate fetch เท่าที่ runtime รองรับ
- upstream fail แล้วมี previous value ให้ตอบ `stale`
- คุมจำนวน rows และลบ LRU/expired rows เป็นระยะจาก request path แบบมี budget
- content hash ต้อง deterministic เหมือน `payload_version()` ปัจจุบัน

### 1.4 R2 tile cache

- key ประกอบด้วย source, frame/layer, z, x, y และ rendering options
- metadata เก็บ `fetched_at`, `expires_at`, content type และ source URL version
- radar frame เก่าและ terrain ใช้ long TTL
- traffic ใช้ short TTL
- GISTDA ใช้ TTL ตาม layer
- จำกัด object age/namespace เพื่อไม่ให้ storage โตโดยไม่จำกัด

**เกณฑ์ผ่าน:** unit tests ครบสำหรับ retry, cache hit/miss/stale, version hash, eviction, binary content type และ secret redaction

## 8. Phase 2: ย้าย source adapters และ API routes

ย้ายเป็นกลุ่ม เพื่อลด blast radius และเปรียบเทียบกับ Python ได้ทีละชุด

### 2.1 กลุ่มไม่มี secret และ JSON ขนาดเล็ก

- `/api/v1/health`
- `/api/v1/provinces`
- `/api/v1/areas`
- `/api/v1/weather/current`
- `/api/v1/river`
- `/api/v1/dams`
- `/api/v1/dams/photos`

### 2.2 กลุ่มข้อมูลสถานีและ normalization ซับซ้อน

- `/api/v1/flood-points`
- `/api/v1/thaiwater/{layer}`
- `/api/v1/river-flow`
- `/api/v1/alerts`
- `/api/v1/sources/status`

ต้องย้ายตรรกะต่อไปนี้แบบ behavior-preserving:

- bbox validation และ filtering
- stale station filtering
- severity mapping
- `alerts_only`
- station twin/deduplication
- metadata counts
- `observed_at_max`
- ETag และ `If-None-Match` เป็น `304`

### 2.3 กลุ่ม content feeds

- `/api/v1/local-news`
- `/api/v1/local-social`

ตรวจ XML/HTML parsing, character encoding, Thai text matching และ source URL validation ด้วย fixture ก่อนเปิด upstream จริง

### 2.4 กลุ่มใช้ secret

- `/api/v1/gistda/tiles/{layer}/{z}/{x}/{y}.png`
- `/api/v1/traffic/status`
- `/api/v1/traffic/tiles/{z}/{x}/{y}.png`
- `/api/v1/forecast/provinces`
- `/api/v1/forecast/tambon`
- `/api/v1/forecast/hourly`

ตั้ง hosted environment keys:

- `GISTDA_API_KEY`
- `TOMTOM_API_KEY`
- `TMD_API_KEY`
- non-secret tuning values เช่น timeout, retries, TTL และ quota reserve

ห้ามเก็บค่าจริงใน source, prompt, test fixture, build log หรือ `.openai/hosting.json`

### 2.5 กลุ่ม binary tiles และ rate limit สูง

- `/api/v1/radar/latest`
- `/api/v1/radar/tiles/{frame}/{z}/{x}/{y}.png`
- `/api/v1/terrain/tiles/{z}/{x}/{y}.png`

ข้อกำหนด radar:

- allowlist เฉพาะ frame ที่ metadata ปัจจุบันประกาศ
- จำกัด zoom และ tile coordinate เหมือนเดิม
- distributed limit ต่ำกว่า RainViewer quota
- R2 cache ต้องแชร์ผลระหว่างผู้ใช้
- concurrent request ของ tile เดียวกันต้องไม่ทำให้ upstream burst
- หาก budget หมด ให้ตอบ `503` พร้อม `Retry-After`

**เกณฑ์ผ่าน:** ทุก route มี contract test เทียบ Python, validation test, upstream failure test และ no-secret log test

## 9. Phase 3: Frontend migration

### 3.1 ย้ายไฟล์โดยไม่ redesign

- ย้าย `frontend/*` ไป static/public directory ของ starter
- คง relative API URLs
- คง Leaflet, fonts และ external map tiles หลังตรวจ Content Security Policy
- คง localStorage key เช่น `marker-mode`
- คง mobile layout และ keyboard behavior

### 3.2 ปรับ polling ให้เหมาะกับ hosted runtime

- หยุด polling เมื่อ tab hidden
- กัน request ซ้อนต่อ source
- abort request เก่าเมื่อ viewport เปลี่ยน
- debounce `moveend`
- ใช้ ETag เพื่อให้ unchanged poll ตอบ `304`
- แสดง stale/error/not-configured ตาม metadata เดิม

### 3.3 Browser acceptance flows

1. เปิดหน้าประเทศไทยและโหลดชั้นข้อมูลเริ่มต้น
2. เลื่อน/ซูมแผนที่และตรวจว่า response เก่าไม่วาดทับใหม่
3. เปิด radar timeline และเล่นหลาย frame
4. คลิกจุดเพื่อโหลด weather และ river data
5. เลือกจังหวัด/อำเภอ/ตำบลและโหลด forecast
6. เปิด/ปิด GISTDA, traffic และ terrain
7. จำค่าตัวกรองหลัง reload
8. แสดง fallback เมื่อ upstream หนึ่งแหล่งล่ม

**เกณฑ์ผ่าน:** desktop และ mobile flows ทำงานเทียบเท่าระบบเดิม ไม่มี mixed content, CORS error หรือ secret ใน browser network log

## 10. Phase 4: Verification และ capacity test

### 4.1 Contract test strategy

- ใช้ request fixture เดียวกันกับ Python และ TypeScript
- normalize เฉพาะค่าที่เปลี่ยนตามเวลา เช่น `fetched_at`
- เปรียบเทียบ status, schema, counts, severity, timestamps และ headers
- response ที่เป็น tile เปรียบเทียบ content type, non-empty bytes และ cache behavior ไม่บังคับ byte-identical หาก upstream เปลี่ยน

### 4.2 Live upstream verification

- รันจาก production Sites runtime เพราะบาง upstream อาจจำกัดประเทศหรือ cloud IP
- ตรวจ RainViewer, ThaiWater, DPM, RID, Open-Meteo, GISTDA, TMD, TomTom, PRD, YouTube และ Wikimedia
- บันทึก upstream status, latency p50/p95, payload size และ error rate
- หาก upstream ใดบล็อก Sites egress ให้ตัดสินใจราย source ว่าจะคง external proxy หรือปิด feature

### 4.3 Load scenarios

- ผู้ใช้ 1, 10 และ 50 sessions เปิดหน้าเดียวกัน
- cold cache เทียบ warm cache
- pan/zoom พร้อมกัน
- radar animation หลาย users
- upstream timeout และ `429`
- D1/R2 temporary error
- deploy ใหม่ระหว่าง cache warm

ตัวชี้วัด:

| ตัวชี้วัด | เป้าหมายเริ่มต้น |
|---|---|
| Health response p95 | < 500 ms |
| Cached JSON p95 | < 1 s |
| Live upstream JSON p95 | < upstream timeout 12 s |
| Duplicate upstream calls ต่อ cache key ในหนึ่ง TTL | ใกล้ 1; ต้องไม่มี burst ตามจำนวนผู้ใช้ |
| Secret leakage | 0 |
| Contract mismatch ระดับ critical/high | 0 |
| Browser flow ผ่าน | 100% ของ 8 flows |

## 11. Phase 5: Deploy และ rollout

### 5.1 Private candidate

- Save version โดยยังไม่ deploy เมื่อทำ review รอบแรก
- ตรวจ source diff และ D1 migrations
- Deploy เป็น production URL แบบ owner-only/private
- ตรวจ URL จากมุมมอง visitor จริง
- ตรวจ analytics, logs, environment bindings และ source status

### 5.2 Limited rollout

- เปิดให้เฉพาะผู้ทดสอบหรือ workspace ตามสิทธิ์ที่ account รองรับ
- รันคู่กับ FastAPI อย่างน้อยหนึ่งรอบข้อมูลจริงของทุก source
- เก็บ mismatch และแก้ก่อน public
- ทดสอบ rollback ไป saved version ก่อนหน้า

### 5.3 Public rollout

- เปิด public เฉพาะเมื่อเจ้าของอนุมัติ
- ตรวจ privacy notice, data attribution และข้อจำกัดการตัดสินใจจากข้อมูล
- ตรวจว่าไม่มี `.env`, key, internal logs หรือ test fixture ถูกเสิร์ฟ
- เปิด custom domain ภายหลังได้ ไม่เป็น blocker ของ release แรก

### 5.4 Rollback

- เปลี่ยน access กลับเป็น owner-only หากพบข้อมูลผิดหรือ secret leak
- restore saved version ก่อนหน้า
- คง external FastAPI deployment/วิธีรันเดิมจน Site เสถียร
- rollback schema ต้องเป็น forward-compatible migration; ห้ามลบ cache table แบบ destructive ใน release แรก

## 12. ชุดทดสอบขั้นต่ำ

| ชั้น | สิ่งที่ต้องทดสอบ |
|---|---|
| Unit | normalization, severity, freshness, geo, retry, hash, cache, limiter |
| Contract | ทุก `/api/v1/*` เทียบ Python fixtures |
| Integration | D1/R2, environment bindings, conditional request, stale fallback |
| Security | secret redaction, SSRF resistance, tile allowlist, input bounds |
| Browser | 8 user flows, desktop/mobile, keyboard, hidden-tab polling |
| Load | cold/warm cache, concurrency, radar budget, upstream failure |
| Deployment | save, private deploy, bindings, rollback, access control |

## 13. ความเสี่ยงและวิธีลด

| ความเสี่ยง | ผลกระทบ | วิธีลด |
|---|---|---|
| Upstream บล็อก cloud egress | บาง layer ใช้ไม่ได้ | ทำ Phase 0 spike; ใช้ external proxy เฉพาะ source ที่จำเป็น |
| D1 contention จาก tile/rate-limit traffic | latency สูง | เก็บ binary ใน R2; ลด D1 writes; batch/expire แบบมี budget |
| Radar quota ถูกใช้เร็วจากหลาย instance | radar ขาดช่วง | distributed limiter, R2 cache, request coalescing, zoom cap |
| Runtime timeout กับ national datasets | `5xx` | cache national payload, แบ่ง parsing, ลด retry, วัด p95 จริง |
| Response ใหญ่เกิน runtime/client | layer โหลดไม่ได้ | gzip/runtime compression, `alerts_only`, bbox filtering, payload budget tests |
| Logic port แล้วค่าระดับเปลี่ยน | ผู้ใช้ตีความผิด | golden contract tests และ dual-run comparison |
| Secret รั่วใน tile URL/error | key ถูกนำไปใช้ | server-side proxy, redaction, no-secret tests |
| Sites beta เปลี่ยน runtime behavior | deploy พัง | pin starter/dependencies, save versions, keep FastAPI fallback |

## 14. ลำดับงานและประมาณการ

| Phase | งาน | ประมาณการ |
|---|---|---|
| 0 | baseline, Sites starter, vertical slice | 1–2 วัน |
| 1 | TypeScript foundation, D1/R2 cache | 2–3 วัน |
| 2 | port adapters และ routes | 5–8 วัน |
| 3 | frontend migration และ browser tests | 2–3 วัน |
| 4 | contract/live/load verification | 2–4 วัน |
| 5 | private rollout, limited rollout, docs | 1–2 วัน |
| รวม | หาก upstream ใช้จาก Sites ได้ทั้งหมด | 13–22 วันทำการ |

ประมาณการนี้ไม่รวมเวลารอ API key, workspace permission, public publishing approval หรือการแก้ upstream ที่บล็อก cloud IP

## 15. Definition of Done

- Sites build และ save version สำเร็จจาก local project
- Private deployment เปิดได้จาก production URL
- ทุก endpoint ที่ frontend ใช้มี TypeScript implementation
- Contract tests ผ่านทั้งหมดโดยไม่มี critical/high mismatch
- D1/R2 cache, stale fallback และ rate limiter ผ่าน integration/load tests
- secrets อยู่ใน hosted environment settings เท่านั้น
- source status บอก `live`, `cached`, `stale`, `not_configured`, `partial`, `error` ตามจริง
- desktop/mobile browser flows ผ่าน
- rollback ไป saved version ก่อนหน้าผ่านการทดสอบ
- README อธิบาย local development, Sites deploy, secrets, storage และ rollback
- เจ้าของระบบอนุมัติ audience ก่อนเปลี่ยนจาก private

## 16. Decision gates

### Gate A: หลัง Phase 0

เลือก full Sites ต่อเมื่อ:

- outbound access ใช้งานกับ source หลัก
- D1/R2 และ binary response ทำงาน
- runtime limits รองรับ weather vertical slice

ถ้าไม่ผ่าน ให้ใช้ hybrid: Sites frontend + external FastAPI backend

### Gate B: หลัง Phase 2

เปิด frontend migration ต่อเมื่อ:

- route contract ผ่านครบสำหรับ source หลัก
- radar limiter และ R2 cache ผ่าน concurrency test
- ไม่มี secret leakage

### Gate C: ก่อน public

ต้องมี:

- live upstream verification
- browser acceptance ผ่าน
- rollback ผ่าน
- data attribution และ privacy review
- การอนุมัติจากเจ้าของระบบ

## 17. งานแรกเมื่อเริ่มพัฒนา

1. รัน Python test suite และสร้าง sanitized contract fixtures
2. สร้าง private Sites starter จาก project นี้
3. ผูก D1/R2 โดยให้ Sites สร้าง `.openai/hosting.json`
4. ทำ `/api/v1/health` และ `/api/v1/weather/current`
5. Deploy private vertical slice
6. ตัดสิน Gate A ก่อน port service ที่เหลือ

## 18. เอกสารอ้างอิง

- [Sites documentation](https://learn.chatgpt.com/docs/sites)
- [Build and deploy internal apps](https://learn.chatgpt.com/use-cases/build-and-deploy-internal-apps)
