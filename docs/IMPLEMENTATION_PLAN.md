# Implementation Plan: ข้อมูลน้ำที่ถูกต้อง สด และเชื่อถือได้

> **สถานะ:** P0 ✅ (วัดจริง 27 ก.ย. 15:23–15:43; ฝนรายชั่วโมงยังต้องวัดเพิ่ม) · P1 ✅ (I-1 ถึง I-5 แก้แล้ว 27 ก.ย. 2026) · P2 backend ✅ (TTL ตั้งจากผล P0 แล้ว, แก้เวลา UTC ของ ปภ. แล้ว; ส่วน frontend `alerts_only` รอ `app.js` ว่าง) · P3–P5 ยังไม่เริ่ม
> **รวมจาก:** `IMPLEMENTATION_PLAN.md` (ความถูกต้อง/ประสิทธิภาพ, ตรวจกับ API สด) + `REFRESH_IMPLEMENTATION_PLAN.md` (ความสดของข้อมูล)
> **หลักการจัดลำดับ:** ความถูกต้อง > ความทนทาน > ความสด > ฟีเจอร์เพิ่ม

## 1. เป้าหมาย

1. ผู้ใช้แยกได้ว่าข้อมูลใดเป็นค่าที่วัดจริง ค่าพยากรณ์ หรือข้อมูลที่ยังไม่พร้อมใช้
2. แผนที่แสดงจุดสำคัญครบ โดยไม่โหลดข้อมูลซ้ำหรือส่งข้อมูลเกินจำเป็น
3. Dashboard ใช้ข้อมูลฝน แม่น้ำ และเขื่อนที่ API ส่งมาแล้วได้เต็มประโยชน์
4. แหล่งข้อมูลหนึ่งล่มแล้วส่วนอื่นยังทำงาน และสถานะข้อมูลแสดงตามจริง
5. ความล่าช้าของข้อมูลที่ผู้ใช้เห็นใกล้กับรอบอัปเดตจริงของต้นทาง โดยภาระต่อ upstream ไม่เพิ่มตามจำนวนผู้ใช้

| ตัวชี้วัด | ปัจจุบัน | เป้าหมาย |
|---|---|---|
| ความล่าช้าสูงสุด ชั้นเร็ว (คลอง, ประตูระบายน้ำ, น้ำท่วมถนน, เรดาร์) | ~20–25 นาที | ≤ รอบต้นทาง + 2 นาที (~7 นาที) |
| ความล่าช้าสูงสุด ชั้นรายชั่วโมง (แม่น้ำ, ฝน 24 ชม.) | ~75 นาที | ≤ 65 นาที (ถูกจำกัดโดยต้นทาง) |
| Payload ชั้นฝนตอนซูมระดับประเทศ | ~2.6 MB | < 200 KB (`alerts_only` + GZip) |
| Upstream request ต่อชั้นต่อชั่วโมง | ไม่คงที่ (flood-points แยกตาม bbox) | คงที่ = 3600 / TTL ไม่ขึ้นกับจำนวนผู้ใช้ |
| Request จาก tab ที่ถูกซ่อน | poll ต่อทุก 5 นาที | 0 |
| Marker ถูกวาดใหม่ทั้งที่ข้อมูลไม่เปลี่ยน | ทุกรอบ poll | 0 |

**ไม่อยู่ในขอบเขต:** การติดตามรายนาทีจริง ซึ่งต้องมี feed ตรงจากเซนเซอร์ของหน่วยงาน (ดูหัวข้อ 10)

## 2. ข้อมูลตั้งต้นจากการตรวจ

### 2.1 แหล่งข้อมูล (snapshot 27 ก.ย. 2026 ไม่ใช่ค่าคงที่ ห้ามฝังใน UI)

| แหล่งข้อมูล | สถานะและขนาดที่พบ | การใช้ปัจจุบัน |
|---|---|---|
| RainViewer | ใช้งานได้; radar หลาย frame | แผนที่และ timeline |
| RID + DPM | เขื่อน 35 แห่ง | จุดและ popup; ใช้เกณฑ์ความรุนแรงร่วมกับน้ำท่วม |
| DPM | จุดแม่น้ำ 378, ถนน 107 | แผนที่; เรียกตาม viewport |
| ThaiWater | ระดับน้ำ 793, ฝน 4,436, คลอง 219, ประตูน้ำ 12 | แผนที่; ฝนระดับประเทศ ~2.6 MB ต่อ response |
| Open-Meteo Weather | ปัจจุบัน + พยากรณ์ฝนรายชั่วโมง 48 ชม. | แสดงเฉพาะฝนปัจจุบันเมื่อคลิก |
| Open-Meteo Flood | `river_discharge` และ `river_discharge_max` 7 วัน | แสดงค่าปัจจุบัน และค่าสูงสุดที่คำนวณจากชุดข้อมูลผิด |
| GISTDA | ยังไม่ตั้งค่า endpoint | ตอบ GeoJSON ว่าง แต่สวิตช์ขอบเขตน้ำท่วมเปิดอยู่ |

### 2.2 ความสดของข้อมูล (จากโค้ด)

**ความล่าช้าสูงสุด = รอบต้นทาง + backend TTL + รอบ poll ของ frontend**

| Endpoint / ชั้น | Backend TTL | Frontend poll | หมายเหตุ |
|---|---|---|---|
| `/radar/latest` | 300 s (`cache_ttl_seconds`) | 5 นาที | |
| `/flood-points` | 300 s **ต่อ bbox key** | 5 นาที + ทุก `moveend` | key ปัด bbox 0.01°: viewport ใหม่ยิง ArcGIS ใหม่ และ bbox ใกล้กันอาจชน key เดียวกัน |
| `/thaiwater/{canal,watergate}` | 300 s | 5 นาที + ทุก `moveend` | cache ทั้งประเทศ กรองใน process |
| `/thaiwater/{water-level,rain}` | 600 s | 5 นาที + ทุก `moveend` | |
| `/dams` | 3600 s | 60 นาที | เหมาะสมแล้ว |
| `/weather/current`, `/river` | 300 / 3600 s ต่อพิกัด | ตอนคลิก | key ต่อพิกัด 0.001° ไม่มีขีดจำกัด |

## 3. ปัญหาที่ต้องแก้ (อ้างอิงจาก phase ต่าง ๆ)

| # | ปัญหา | ความรุนแรง | Phase |
|---|---|---|---|
| I-1 | "ค่าสูงสุด 7 วัน" คำนวณจาก `river_discharge` แทน `river_discharge_max` และไม่บอกว่าเป็นพยากรณ์ | สูง: ตัวเลขผิด | 1 |
| I-2 | GISTDA ไม่ได้ตั้งค่า แต่สวิตช์เปิดอยู่ แผนที่ว่างอาจถูกเข้าใจว่าไม่มีน้ำท่วม | สูง: ตีความผิด | 1 |
| I-3 | % น้ำในเขื่อนสูงถูกแสดงด้วยเกณฑ์เดียวกับน้ำท่วม ("วิกฤต") | สูง: ตีความผิด | 1 |
| I-4 | ชั้น ThaiWater แม่น้ำเข้าใจผิดว่า upstream ส่งเฉพาะสถานีล้นตลิ่ง (~63) ความจริงส่งทั้งหมด 793 → `minZoom: 0` ทำให้วาดครบทุกจุดตอนซูมออก, ป้าย "สถานีน้ำมาก/ล้นตลิ่ง" และ README ผิด | กลาง | 1 |
| I-5 | ปุ่ม ↻ แสดง "รีเฟรชข้อมูลแล้ว" เสมอ แม้ทุก request ล้ม (loader กลืน error) | กลาง | 1 |
| I-6 | Stale fallback คืนจุด ThaiWater ที่หมดอายุแล้วได้ เพราะไม่ตรวจ `observed_at` ซ้ำ | กลาง | 2 |
| I-7 | `/flood-points` cache ต่อ bbox: upstream call โตตามผู้ใช้ และ bbox ใกล้กันอาจคืนจุดผิดขอบ | กลาง | 2 |
| I-8 | `AsyncTTLCache` ไม่มีขีดจำกัดจำนวน key: memory โตเรื่อย ๆ | กลาง | 2 |
| I-9 | ArcGIS `resultRecordCount=2000` ไม่ตรวจ `exceededTransferLimit`: จุดอาจหายเงียบ | กลาง | 2 |
| I-10 | `moveend` ยิง request ทุกชั้นโดยไม่มี debounce หรือ abort: response เก่าวาดทับใหม่ได้ | กลาง | 3 |
| I-11 | `setInterval` 4 ตัว: ไม่รู้สถานะ tab, ไม่กัน request ซ้อน, วาดใหม่ทุกรอบ | ต่ำ | 3 |
| I-12 | ไม่มีการแสดงอายุข้อมูลรายชั้น | ต่ำ | 3 |
| I-13 | สถานี DPM กับ ThaiWater ซ้ำกัน KPI อาจนับซ้ำ | ต่ำ | 4 |

## 4. Phase 0: วัดรอบอัปเดตจริงของต้นทาง (เริ่มคู่ขนานได้ทันที)

ตั้ง TTL จากข้อมูลจริง ไม่ใช่จากการเดา

- **สคริปต์:** `scripts/probe_cadence.py` ยิงแต่ละ upstream ทุก 60 วินาที นาน 3 ชั่วโมง (ควรมีช่วงฝนตก) บันทึก `max(observed_at)`, hash ของ payload, ขนาด response และเวลาตอบ ลง CSV
- **ผลลัพธ์:** ตารางต่อชั้นที่มี (a) ระยะห่างระหว่าง `observed_at` ที่เปลี่ยน (median, p90), (b) ความล่าช้าในการเผยแพร่ = เวลาที่เห็น − `observed_at`, (c) ขนาด payload ใช้เป็น baseline ของ Phase 2
- **ต้องรันบนเครื่องในไทย:** cloud workspace เข้า upstream ไม่ได้ และ BMA บาง endpoint จำกัด IP ไทย
- **กฎตั้ง TTL:** `TTL = clamp(median_cadence / 3, 60 s, 600 s)`

**งาน ~0.5 วัน** (ส่วนใหญ่เป็นเวลารอเก็บข้อมูล) · **ไฟล์:** `scripts/probe_cadence.py` (ใหม่)

## 5. Phase 1: ความถูกต้องและความชัดเจนของข้อมูล (P0)

### 1.1 แสดงสถานะแหล่งข้อมูลตามจริง (I-2)

- เพิ่มสถานะ `live`, `cached`, `stale`, `not_configured`, `error` พร้อม `fetched_at` และเวลาสังเกตล่าสุดใน response โดยคงรูปแบบ `data` เดิม frontend เดิมจึงไม่พัง (รูปแบบ `meta` ดู Phase 2 ข้อ 2.4)
- GISTDA ยังไม่ตั้งค่า: ปิดสวิตช์ขอบเขตน้ำท่วมและแสดงเหตุผล ห้ามแสดงพื้นที่ว่างเหมือนยืนยันว่าไม่มีน้ำท่วม
- แยก `/api/v1/health` (ความพร้อมของแอป) ออกจาก `/api/v1/sources/status` (สถานะ cache และการดึงล่าสุดต่อแหล่ง) endpoint หลังไม่ยิง upstream ตามทุก request
- Popup และภาพรวมแสดงเวลาที่วัดจริง ไม่ใช้เวลาที่ backend fetch แทน

**เกณฑ์รับงาน:** ปิด GISTDA แล้ว UI บอกว่าไม่พร้อมใช้; upstream ล่มแล้ว UI บอกว่าใช้ข้อมูลเก่าหรือผิดพลาด; ทุกจุดแสดงเวลาสังเกตจริง

### 1.2 แก้การแสดงค่าพยากรณ์และระดับเขื่อน (I-1, I-3)

- ใช้ `river_discharge_max` สำหรับค่าสูงสุดรายวัน แสดงรายการหรือกราฟ 7 วันพร้อมวันที่ และติดป้าย **พยากรณ์** ไม่ใช่ระดับน้ำท่วมที่วัด ณ จุดคลิก
- ใช้ `hourly.precipitation` และ `precipitation_probability` ที่โหลดอยู่แล้ว ทำกราฟฝน 48 ชม. เวลาท้องถิ่น แยกค่าปัจจุบันออกจากค่าพยากรณ์
- เปลี่ยน % น้ำในเขื่อนเป็นสถานะปริมาณกักเก็บ (`ต่ำ`, `ปกติ`, `สูง`, `สูงมาก`) แยกจากระดับน้ำท่วม ระบุว่าเป็นเกณฑ์แสดงผลของโครงการจนกว่าจะยืนยันเกณฑ์ทางการ
- แยกป้าย "น้ำล้นตลิ่ง/น้ำมาก" ออกจาก "น้ำท่วมขังถนน" เพราะระดับน้ำสูงในแม่น้ำไม่เท่ากับน้ำท่วมพื้นที่รอบสถานี

**เกณฑ์รับงาน:** ค่า `river_discharge_max` ตรงกับ response ของ API; เขื่อนกักเก็บสูงไม่ถูกเรียกว่า "น้ำท่วมวิกฤต"; ข้อมูลพยากรณ์มีป้ายและวันที่ชัด

### 1.3 แก้ชั้น ThaiWater ระดับน้ำแม่น้ำ (I-4)

- `THAIWATER_LAYERS["water-level"].minZoom` เปลี่ยนจาก `0` เป็น `8` ตอนซูมออกจะแสดงเฉพาะ `moderate` ขึ้นไป
- ป้ายชั้นเปลี่ยนเป็น "ระดับน้ำแม่น้ำ" และคำอธิบายเป็น "สถานีโทรมาตรทั่วประเทศ"
- แก้ README และ docstring ใน `thaiwater.py` ที่อ้างว่า upstream ส่งเฉพาะสถานีล้นตลิ่ง
- ทบทวนเกณฑ์สีด้วยการกระจายของ `storage_percent` จริงทั้ง 793 สถานี

**เกณฑ์รับงาน:** ซูมระดับประเทศแล้วไม่วาดทั้ง 793 จุด; ข้อความใน UI และ README ตรงกับข้อมูลจริง

### 1.4 ทำให้ส่วนควบคุม Dashboard ตรงกับความจริง (I-5)

ปุ่ม ↻, เมนูมือถือ, ปุ่มกลับมุมมองประเทศไทย และ KPI ผูกกับการทำงานแล้วใน `app.js` ปัจจุบัน สิ่งที่เหลือ:

- **รีเฟรชทั้งชุด:** loader คืนผลสำเร็จหรือล้มเหลว แทนการกลืน error ปุ่ม ↻ แสดงผลรายแหล่ง เช่น "รีเฟรช 5/6 แหล่ง · ThaiWater ฝนล้มเหลว" และไม่เรียกว่า "ข้อมูลใหม่" ถ้ายังได้ cache เดิม
- **KPI ระบุขอบเขตชัด:** เช่น "จุดเตือนในหน้าจอ" กับ "จุดเตือนทั่วประเทศ" และยังไม่รวม DPM กับ ThaiWater จนกว่าจะทำ Phase 4
- **ตรวจ keyboard:** ทุกปุ่มและสวิตช์ใช้ได้ด้วยคีย์บอร์ด

**เกณฑ์รับงาน:** ทุกปุ่มทำงานด้วยเมาส์และคีย์บอร์ด; KPI ตรวจย้อนกับ API ได้; การรีเฟรชไม่บอกว่าสำเร็จเมื่อ request ล้ม

**งาน Phase 1 ~1.5 วัน** · **ไฟล์:** `routes.py`, `upstreams.py`, `thaiwater.py`, `frontend/app.js`, `index.html`, `styles.css`, `README.md`

## 6. Phase 2: Backend data layer (P1)

รวมงาน cache, ความทนทาน และ refresh policy ไว้ใน phase เดียว เพราะแก้ไฟล์ชุดเดียวกัน

### 2.1 Cache ทั้งประเทศ + LRU (I-7, I-8)

- **`/flood-points` ดึงทั้งประเทศครั้งเดียว:** query ArcGIS ด้วยกรอบประเทศไทย (97,5,106,21) cache key เดียว `flood-points:national` แล้ว `filter_bbox()` ใน process ผลคือ upstream call คงที่ 2 ครั้งต่อ TTL ไม่ขึ้นกับผู้ใช้หรือการเลื่อนแผนที่ และไม่มีปัญหา bbox ชน key
- **`AsyncTTLCache(max_entries=512)`:** evict แบบ LRU และคง request coalescing ไว้
- **Inject clock:** `clock: Callable[[], float] = time.monotonic` ให้ test เลื่อนเวลาได้โดยไม่ต้อง sleep
- **นับสถิติ:** hit, miss, stale และ upstream error ต่อ prefix แล้วเปิดดูผ่าน `/api/v1/sources/status`

### 2.2 TTL แยกรายชั้น รวมไว้ที่เดียว

เพิ่มใน `backend/app/config.py` override ผ่าน env ได้ ค่าตั้งต้นด้านล่างปรับตามผล Phase 0

```python
class Settings(BaseSettings):
    ...
    # seconds; override e.g. REFRESH_TTL='{"canal": 60}'
    refresh_ttl: dict[str, int] = {
        "radar": 120, "flood-points": 120, "canal": 90, "watergate": 90,
        "water-level": 600, "rain": 600, "gistda": 900,
        "weather": 300, "river": 3600, "dams": 3600,
    }
```

- **`routes.py`:** แทน `settings.cache_ttl_seconds` และ `3600` ที่ hard-code ด้วย `settings.refresh_ttl[layer]`
- **`thaiwater.py`:** เลิกใช้ `ThaiWaterLayer.ttl_seconds` ให้ TTL มีแหล่งเดียว

### 2.3 ลดขนาด payload

- **GZip:** เปิด `GZipMiddleware(minimum_size=1024)` สำหรับ JSON ขนาดใหญ่
- **กรองใน backend:** เพิ่ม query `alerts_only` หรือ `severity` ให้ `/api/v1/thaiwater/{layer}` และ `/api/v1/flood-points` กรองจาก dataset ที่ cache แล้ว และตอบ `total_national`, `total_in_bbox`, `returned_count`
- **ใช้ตาม zoom:** ซูมออกขอเฉพาะจุดเตือน ซูมเข้าขอทุกจุดใน viewport ด้วย threshold เดิมที่ UI ใช้ ย้ายตรรกะ `minZoom` จาก frontend มาเป็นพารามิเตอร์นี้

### 2.4 Metadata ความสด + conditional response

```json
{ "data": {...}, "stale": false,
  "meta": { "status": "live", "fetched_at": "2026-09-27T14:50:12+07:00",
            "observed_at_max": "2026-09-27 14:45",
            "ttl_seconds": 90, "version": "a1b2c3d4",
            "total_national": 4436, "total_in_bbox": 812, "returned_count": 37 } }
```

- **`version`:** hash สั้นของ payload ที่ normalize แล้ว คำนวณครั้งเดียวตอนโหลดเข้า cache
- **ETag:** ส่ง header `ETag` = hash(version + bbox ที่ปัดแล้ว + filter) และรองรับ `If-None-Match` ถ้าตรงกันให้ตอบ `304` โดยไม่มี body ใช้คู่กับ GZip: GZip ลดขนาดตอนข้อมูลเปลี่ยน ETag ตัด body ตอนข้อมูลไม่เปลี่ยน

### 2.5 ความทนทาน (I-6, I-9)

- **Stale fallback:** ตรวจอายุ `observed_at` ซ้ำก่อนส่งจุด ThaiWater ข้อมูลที่เกินอายุห้ามกลับมาแสดงเพียงเพราะ cache ยังอยู่
- **ArcGIS:** ตรวจ `exceededTransferLimit` และ paginate ด้วย `resultOffset` จุดจะได้ไม่หายเงียบ
- **ผลบางส่วน:** DPM แม่น้ำ/ถนน และ RID/DPM เขื่อน คืนผลบางส่วนพร้อมสถานะรายแหล่งเมื่อแหล่งหนึ่งล่ม (`asyncio.gather(..., return_exceptions=True)`) join ไม่ครบต้องไม่แปลว่า "ไม่มีเขื่อน"
- **Retry:** จำกัดสำหรับ error ชั่วคราว (timeout, 5xx, 429 แบบ exponential backoff) ไม่ retry 4xx หรือ `not_configured`

**เกณฑ์รับงาน:** cache มีขนาดจำกัด; เลื่อน viewport สองครั้งเรียก upstream 1 ครั้ง; bbox ใกล้กันไม่คืนจุดผิดขอบ; payload ฝนซูมประเทศ < 200 KB; request ที่สองได้ `304`; upstream หนึ่งล่มแล้วอีกแหล่งยังแสดงพร้อมป้าย partial; จำนวนจุดไม่ถูกตัดเงียบ

**งาน Phase 2 ~1.5 วัน** · **ไฟล์:** `config.py`, `cache.py`, `routes.py`, `upstreams.py`, `thaiwater.py`, `main.py`, tests

## 7. Phase 3: Frontend loading + ความสดของข้อมูล (P1)

### 3.1 ตัวจัดการ poll กลาง (I-10, I-11)

แทนที่ `setInterval` 4 ตัวด้วย `frontend/refresh.js` (ใหม่) ให้ `app.js` เล็กลง

```js
// sketch — interval ต่อชั้นมาจากผล Phase 0
const scheduler = createScheduler([
  { name: "canal",        load: () => loadThaiWater("canal"), everyMs: 60_000 },
  { name: "flood-points", load: loadFloodPoints,              everyMs: 60_000 },
  { name: "radar",        load: loadRadar,                    everyMs: 120_000 },
  { name: "rain",         load: () => loadThaiWater("rain"),  everyMs: 300_000 },
  { name: "dams",         load: loadDams,                     everyMs: 3_600_000 },
]);
```

| พฤติกรรม | รายละเอียด |
|---|---|
| **ใช้ `setTimeout` แบบต่อกันเป็นทอด + jitter ±10%** | รอบใหม่เริ่มหลังรอบก่อนจบ ไม่มี request ซ้อน และผู้ใช้หลายคนไม่ยิงพร้อมกัน |
| **`AbortController` ต่อชั้น** | request ใหม่ยกเลิกอันเก่า และทิ้ง response ที่มาถึงผิดลำดับ |
| **Debounce `moveend` 250 ms** | ลาก/ซูมต่อเนื่องยิงครั้งเดียว |
| **ส่ง `If-None-Match`** | ได้ `304` หรือ `version` เดิมก็ไม่ต้องวาดใหม่ แผนที่ไม่กระพริบ และ popup ที่เปิดอยู่ไม่ปิดเอง |
| **`visibilitychange`** | tab ซ่อนก็หยุดทุก timer กลับมาแล้วโหลดทันทีเฉพาะชั้นที่อายุเกิน interval |
| **`online` / `offline`** | offline ก็หยุดและแสดงสถานะ online ก็ refresh ทุกชั้น |
| **ชั้นที่ปิดสวิตช์** | ไม่อยู่ในรอบ poll ทุกชั้น |
| **ปุ่ม ↻** | เรียก `scheduler.refreshAll()` แล้วสรุปผลรายแหล่งตาม 1.4 |

**ข้อควรระวัง:** การวาดใหม่ต้องรักษา popup ที่เปิดอยู่ ใช้ `feature.properties.id` เป็น key ถ้า popup ของ id นั้นเปิดอยู่ให้อัปเดตเนื้อหาแทนการลบ marker

### 3.2 แสดงความสดของข้อมูล (I-12)

- **อายุข้อมูลรายชั้น:** ใน `<small>` ของ layer control เช่น "41 สถานี · 3 นาทีที่แล้ว" ใช้ `Intl.RelativeTimeFormat("th")` คำนวณจาก `meta.observed_at_max` และอัปเดตข้อความทุก 30 วินาทีโดยไม่ fetch
- **สถานะ 3 ระดับ** เทียบกับรอบปกติของชั้นจาก Phase 0:

  | สถานะ | เงื่อนไข | แสดงผล |
  |---|---|---|
  | สด | อายุ ≤ 1.5 × รอบปกติ | ปกติ |
  | ล่าช้า | ≤ 3 × รอบปกติ | ตัวอักษรสีส้ม |
  | ขาดช่วง | > 3 × รอบปกติ, `status: stale/error` | สีแดง + "ข้อมูลล่าสุดเมื่อ 14:05" |

- **Header status:** แสดงสถานะของชั้นที่แย่ที่สุด เช่น "ออนไลน์ · คลองล่าช้า" และ badge `#data-freshness` ใช้เวลาล่าสุดของชั้นเร็ว
- **Accessibility:** สถานะใช้ทั้งสีและข้อความ ไม่พึ่งสีอย่างเดียว

**เกณฑ์รับงาน:** เลื่อนแผนที่เร็วไม่แสดงข้อมูลของ viewport เก่า; tab ซ่อน 10 นาทีเรียก 0 ครั้ง; `304` แล้ว marker และ popup ไม่เปลี่ยน; ทุกชั้นแสดงอายุข้อมูลและเปลี่ยนสถานะถูกต้อง

**งาน Phase 3 ~1.5 วัน** · **ไฟล์:** `frontend/refresh.js` (ใหม่), `app.js`, `index.html`, `styles.css`

## 8. Phase 4: ลดความซ้ำและสรุปสถานการณ์ (P2)

### 4.1 จัดความสัมพันธ์ของสถานี (I-13)

- สำรวจจุดซ้ำ DPM river gauge กับ ThaiWater water-level จากรหัสสถานี ชื่อ และพิกัด บันทึกคู่ที่จับได้และกรณีไม่แน่ใจ
- ใช้รหัสสถานีเป็นเกณฑ์แรก ใช้ชื่อ + ระยะพิกัดเฉพาะเมื่อไม่มีรหัส และไม่ merge อัตโนมัติเมื่อข้อมูลขัดกัน
- เลือกค่าที่มี `observed_at` ล่าสุดจากแหล่งที่ตรวจสอบได้ popup แสดงที่มาและค่าทั้งสองแหล่งเมื่อไม่ตรงกัน
- แยกชั้นน้ำท่วมขังถนนออกจากชั้นแม่น้ำ หรือเพิ่มตัวกรองประเภท

**เกณฑ์รับงาน:** จุดที่ยืนยันว่าซ้ำมี marker เดียว; จุดที่ไม่แน่ใจยังแยกกัน; KPI ไม่นับซ้ำ

### 4.2 Endpoint ภาพรวม

- `GET /api/v1/summary` คำนวณจาก cache และ dataset เดียวกับแผนที่: จำนวนเขื่อนตามสถานะกักเก็บ, จุดเตือนตามประเภทและระดับ, ฝนหนัก, เวลาข้อมูลล่าสุด, สถานะแหล่งข้อมูล
- รองรับ national summary และ viewport summary (query bbox) โดย response ระบุ `scope`
- ใช้ endpoint นี้เลี้ยง KPI frontend ไม่ต้องโหลด GeoJSON ทั้งประเทศเพื่อนับ
- รายการ "พื้นที่ที่ต้องดู" เรียงตามความรุนแรงและความใหม่ของข้อมูล คลิกแล้วไปยังจุดบนแผนที่

**เกณฑ์รับงาน:** ตัวเลข summary ตรงกับ dataset ที่แสดง; KPI เปลี่ยนตาม scope; ไม่นับจุดซ้ำ

**งาน Phase 4 ~2 วัน**

## 9. Phase 5 (Optional): Background refresh + Server-Sent Events

ทำเมื่อต้องการความล่าช้าเท่ากับรอบต้นทาง หรือมีผู้ใช้พร้อมกันจำนวนมาก ตัดสินใจหลังวัดผล Phase 3

```
upstream ──(refresher task ต่อชั้น, ทุก TTL)──▶ cache ──▶ version เปลี่ยน?
                                                     └─▶ SSE /api/v1/stream  event: layer-updated {layer, version}
browser EventSource ──▶ fetch เฉพาะชั้นที่เปลี่ยน (endpoint เดิม + ETag)
```

- **Refresher:** เริ่มใน FastAPI `lifespan` 1 `asyncio.Task` ต่อชั้น ใช้ loader และ cache เดิม
- **Lazy:** ทำงานเฉพาะเมื่อมี SSE subscriber ≥ 1 หรือมี request ใน 10 นาทีล่าสุด ไม่มีคนดูก็ไม่ยิง upstream
- **`GET /api/v1/stream`:** ใช้ `StreamingResponse` ส่ง `text/event-stream` พร้อม heartbeat ทุก 25 วินาที และรองรับ `Last-Event-ID`
- **Fallback:** SSE หลุดเกิน 2 ครั้งติดกันให้กลับไปใช้ scheduler ของ Phase 3 โดยใช้โค้ดโหลดชุดเดียวกัน
- **ข้อจำกัด:** ใช้ได้เมื่อรัน process เดียว ถ้ารันหลาย worker หรือหลาย instance ต้องย้าย cache และ pub/sub ไป Redis (README roadmap ข้อ 4)

**งาน ~1.5–2 วัน** · **ไฟล์:** `backend/app/services/refresher.py` (ใหม่), `main.py`, `routes.py`, `frontend/refresh.js`

## 10. การทดสอบ เอกสาร และการตรวจรับ

**Backend (pytest)**
- Normalization, freshness (รวมกรณี stale fallback หมดอายุ), pagination, `alerts_only`, cache (หมดอายุตาม fake clock, LRU), TTL override, ETag/`304`, partial failure, summary, และ response แบบเดิมยังใช้ได้
- `/flood-points`: เลื่อน viewport สองครั้งเรียก loader 1 ครั้ง

**Frontend (Playwright + mocked backend + `page.clock`)**
- ระดับประเทศและกรุงเทพฯ: สวิตช์, popup, timeline, รีเฟรช, mobile, network failure, ช่วง zoom, rapid pan
- เลื่อนเวลา 10 นาที: ชั้นคลองถูกเรียก ~10 ครั้ง ฝน ~2 ครั้ง
- Tab ซ่อน 10 นาทีเรียก 0 ครั้ง, `moveend` 10 ครั้งใน 1 วินาทียิง 1 request ต่อชั้น, ไม่มี console error

**วัดผลจริง (ก่อน/หลัง, เครื่องในไทย, viewport เดิม, 1 ชั่วโมง)**
- Payload, latency, upstream call ต่อชั้นต่อชั่วโมง (ต้องไม่ถี่เกิน TTL), hit ratio
- ความล่าช้าจริง = เวลาที่ marker เปลี่ยนบนจอ − `observed_at`

**เอกสาร**
- schema, หน่วย, timezone, ความหมายของ `severity`, `status`, `stale` และตัวอย่าง response ใน OpenAPI และ README

**ก่อนเผยแพร่จริง**
- ตรวจเงื่อนไขการใช้ RainViewer, ThaiWater, OpenStreetMap tiles และสิทธิ์ GISTDA ตามรูปแบบการเผยแพร่

**เกณฑ์จบงาน:** tests ผ่าน, ไม่มี console error, ข้อมูลเก่าหรือแหล่งล่มแสดงชัด, ข้อมูลตรงกับเวลาและหน่วยของ upstream

## 11. ความเสี่ยงและข้อจำกัด

| ความเสี่ยง | ผลกระทบ | การจัดการ |
|---|---|---|
| ต้นทางจำกัด request หรือบล็อกเมื่อยิงถี่ขึ้น | ชั้นข้อมูลหาย | TTL ขั้นต่ำ 60 s, User-Agent ระบุตัวตน, backoff เมื่อได้ 429/5xx, stale fallback |
| รอบต้นทางช้ากว่าที่คิด | ลด TTL แล้วไม่ได้ข้อมูลใหม่เร็วขึ้น | ตั้ง TTL จากผล Phase 0 และ UX แสดงอายุจริง |
| ฝนทั้งประเทศ 2.6 MB ต่อการ fetch upstream | ใช้ bandwidth ฝั่ง server | TTL ฝน ≥ 300 s; ฝั่ง client ใช้ `alerts_only` + GZip + ETag |
| ArcGIS ทั้งประเทศเกิน 2000 records | ข้อมูลขาดหาย | paginate ด้วย `resultOffset` + test จำนวน |
| รันหลาย worker | cache ซ้ำ, upstream call × N | ใช้ worker เดียวไปก่อน หรือทำ Redis ก่อน Phase 5 |
| popup ปิดเองระหว่างผู้ใช้อ่าน | UX แย่ | ข้ามการวาดเมื่อ version เดิม และอัปเดตเนื้อหา popup แบบคง marker |
| **รายนาทีจริง** | แหล่งสาธารณะอัปเดต 5–60 นาที | ต้องทำข้อตกลงกับสำนักการระบายน้ำ กทม. หรือ สสน. ขอ feed ตรง เป็นงานเจรจา ไม่ใช่งานโค้ด |

## 12. ลำดับการส่งมอบ

| ลำดับ | Phase | งาน | ขึ้นกับ | PR |
|---|---|---|---|---|
| 0 | P0 วัดรอบต้นทาง | 0.5 วัน (รันคู่ขนาน) | — | `scripts/` |
| 1 | P1 ความถูกต้อง (I-1 ถึง I-5) | 1.5 วัน | — | PR #1 |
| 2 | P2 Backend data layer | 1.5 วัน | P0 (ค่า TTL) | PR #2 |
| 3 | P3 Frontend loading + ความสด | 1.5 วัน | P2 (`meta`, ETag, `alerts_only`) | PR #3 |
| 4 | P4 จุดซ้ำ + summary | 2 วัน | P2 | PR #4 |
| 5 | P5 SSE (optional) | 1.5–2 วัน | P2–P3 + ผลวัด | PR #5 |

**รวม P0–P3 ≈ 5 วัน:** ได้ความถูกต้อง ความทนทาน และความล่าช้าชั้นเร็วลดจาก ~20–25 เหลือ ~5–8 นาที ส่วน P4–P5 ตัดสินใจหลังวัดผล

**การทำงานร่วมกัน:** มีมากกว่าหนึ่ง session แก้โปรเจคนี้พร้อมกัน ให้กำหนดเจ้าของต่อ PR ก่อนเริ่ม และแต่ละ PR แก้เฉพาะไฟล์ในขอบเขตของตัวเอง `app.js` และ `routes.py` ถูกแตะเกือบทุก phase จึงต้องทำตามลำดับ ไม่ทำคู่ขนาน

## 13. เรื่องที่ต้องยืนยันก่อนเปิดใช้จริง

- URL, credential และ schema จริงของ GISTDA ถ้าต้องการ polygon น้ำท่วมจากดาวเทียม
- นิยามระดับเตือนเขื่อน ถ้าต้องอิงเกณฑ์ทางการแทนเกณฑ์แสดงผลของโครงการ
- ขอบเขตการเผยแพร่ Dashboard เพื่อทบทวนเงื่อนไขการใช้ข้อมูลและ tile
- จำนวน worker หรือ instance ตอนใช้งานจริง ซึ่งกำหนดว่าต้องมี Redis ก่อน Phase 5 หรือไม่
