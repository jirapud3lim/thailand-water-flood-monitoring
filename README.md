# Thailand Water Flood Monitoring

โครงตั้งต้นสำหรับระบบติดตามน้ำท่วมประเทศไทยแบบ near real-time ซึ่งย้ายแนวคิดจาก
standalone dashboard มาเป็น FastAPI backend และ Leaflet frontend

## ความสามารถใน MVP

- แผนที่ประเทศไทยด้วย Leaflet/OpenStreetMap
- radar ย้อนหลังจาก RainViewer (เลือก frame และเล่น animation ได้)
- สภาพฝนปัจจุบันจาก Open-Meteo เมื่อคลิกบนแผนที่
- river discharge จาก Open-Meteo Flood API (GloFAS) เมื่อคลิกบนแผนที่
- ปริมาณน้ำในเขื่อนขนาดใหญ่จากกรมชลประทาน พร้อม icon และรายละเอียดรายจุด
- จุดเฝ้าระวังน้ำท่วมทั่วประเทศ แบ่งสีตามระดับความรุนแรง
- จุดวัดน้ำท่วมถนนกรุงเทพฯ ซึ่งแสดงความลึกเป็นเซนติเมตรเมื่อแหล่งข้อมูลมีค่า
- สถานีโทรมาตรจากคลังข้อมูลน้ำแห่งชาติ (ThaiWater / สสน.): ระดับน้ำแม่น้ำทั่วประเทศ,
  ฝนสะสม 24 ชม., ระดับน้ำคลอง กทม. และประตูระบายน้ำ (ไม่ต้องใช้ API key)
- endpoint สำหรับ flood GeoJSON แบบ bounding box โดยเตรียม adapter สำหรับ GISTDA
- cache ฝั่ง backend พร้อม stale fallback เพื่อลดการเรียก upstream และลดอาการแผนที่ค้าง
- health endpoint และ automated tests ขั้นต้น

API ที่เพิ่มสำหรับชั้นข้อมูลจุด:

```text
GET /api/v1/dams
GET /api/v1/flood-points?min_lon=...&min_lat=...&max_lon=...&max_lat=...
GET /api/v1/thaiwater/{layer}?min_lon=...&min_lat=...&max_lon=...&max_lat=...
    layer = water-level | rain | canal | watergate
```

ทุก endpoint ข้อมูลตอบรูปแบบเดิม `{data, stale}` และเพิ่ม `meta`:

```json
"meta": {"source": "dams", "status": "live|cached|stale|not_configured",
         "fetched_at": "2026-09-27T15:10:02+07:00", "observed_at_max": "2026-09-27T15:00+07:00",
         "ttl_seconds": 3600}
```

`status = "partial"` หมายถึงบาง feed ย่อยล่ม (เช่น จุดวัดถนนของ ปภ.) แต่ส่วนที่เหลือยังใช้ได้
ถ้าต้นทางล่มและไม่มี cache เลย endpoint ตอบ `503` พร้อม `detail.status = "error"`
ส่วนสถานะของทุกแหล่งดูได้ที่ `GET /api/v1/sources/status` (อ่านจาก cache ไม่ยิงต้นทาง;
มี TTL และตัวนับ hit/miss/stale/error ต่อแหล่ง)

### Cache, payload และ conditional request

- TTL แยกรายแหล่งอยู่ที่ `DEFAULT_REFRESH_TTL` ใน `backend/app/config.py` ตั้งจากผลวัดจริง 27 ก.ย. 2026
  (คลอง 5 นาที → 100 s; แม่น้ำ/ประตูน้ำ/เรดาร์ 10 นาที → 200 s; จุด ปภ. ~15 นาที → 300 s)
  ฝนรายชั่วโมงและแหล่งรายวันยังใช้ค่าเผื่อไว้ จนกว่าจะวัดข้ามต้นชั่วโมงได้
  override บางแหล่งผ่าน env: `REFRESH_TTL='{"thaiwater-canal": 100}'`
- `/flood-points` และ `/thaiwater/*` ดึงข้อมูลทั้งประเทศครั้งเดียวต่อ TTL แล้วกรอง viewport ใน backend
  จำนวนครั้งที่ยิงต้นทางจึงไม่ขึ้นกับจำนวนผู้ใช้หรือการเลื่อนแผนที่
- `?alerts_only=true` คืนเฉพาะจุดระดับเตือน (flood-points: low ขึ้นไป, ThaiWater: moderate ขึ้นไป)
  `meta` บอก `total_national`, `total_in_bbox`, `returned_count`
- Response > 1 KB ถูกบีบอัด GZip และทุก response มี `ETag` + `Cache-Control: no-cache`
  ส่ง `If-None-Match` กลับมาแล้วข้อมูลไม่เปลี่ยนจะได้ `304` ไม่มี body
  (ตัวอย่างฝน 4,436 สถานี: 2.35 MB → 87 KB gzip → 24 KB เมื่อ `alerts_only`)
- Upstream ที่ timeout / 5xx / 429 ถูก retry แบบ backoff (`UPSTREAM_RETRIES`, ค่าเริ่มต้น 1); 4xx อื่นไม่ retry
- ArcGIS ถูกดึงต่อหน้า (`resultOffset`) เมื่อผลเกิน 2,000 จุด
- ThaiWater ตรวจอายุข้อมูลซ้ำทุกครั้งที่ตอบ ข้อมูลเกินอายุจะไม่กลับมาแม้ยังอยู่ใน cache
- เขื่อนที่ RID รายงานแต่จับคู่พิกัดไม่ได้ถูกนับใน `meta.unmatched_count` ไม่หายเงียบ
- เวลา `DATA_DT` ของจุดน้ำท่วมถนน ปภ. เป็น UTC (ตาม metadata ของ layer) backend แปลงเป็นเวลาไทยใน
  `observed_at` และเก็บค่าต้นทางไว้ใน `observed_at_utc`
`/api/v1/health` ตรวจเฉพาะว่าแอปทำงาน

### วัดรอบอัปเดตของต้นทาง

ตั้ง TTL จากข้อมูลจริงด้วยสคริปต์ (ต้องรันจากเครือข่ายในไทย):

```bash
python scripts/probe_cadence.py --interval 60 --duration 3h --out data/cadence.csv
python scripts/probe_cadence.py --summarize data/cadence.csv
```

ชั้น ThaiWater ดึงข้อมูลระดับประเทศครั้งเดียวต่อ TTL (5–10 นาที) แล้วกรองตาม viewport
ใน backend จึงไม่ยิง upstream ทุกครั้งที่เลื่อนแผนที่ สถานีที่ข้อมูลเก่ากว่ากำหนด
(คลอง/ประตูระบายน้ำ 6 ชม., แม่น้ำ 24 ชม., ฝน 26 ชม.) จะถูกตัดทิ้งอัตโนมัติ

| ชั้นข้อมูล | เกณฑ์สี |
|---|---|
| ระดับน้ำแม่น้ำ | % ความจุลำน้ำ: >100 วิกฤต (ล้นตลิ่ง) · ≥90 สูง · ≥70 เฝ้าระวัง · ต่ำกว่านั้นปกติ (upstream ส่งทุกสถานี ~800 จุด; ซูมออกแสดงเฉพาะจุดเฝ้าระวังขึ้นไป) |
| ฝน 24 ชม. | เกณฑ์กรมอุตุฯ: >90 มม. วิกฤต · >35 สูง · >10 เฝ้าระวัง · >0 ฝนเล็กน้อย |
| คลอง กทม. | เทียบระดับเตือนภัย/วิกฤตของสถานี (ถ้าไม่มีเกณฑ์จะเป็นสีเทา) |
| ประตูระบายน้ำ | แสดงระดับหน้า/ท้ายประตู ไม่จัดระดับความรุนแรง |

> River discharge เป็นสัญญาณเชิงอุทกวิทยาความละเอียดประมาณ 5 กม. ไม่ใช่การยืนยัน
> ว่าจุดนั้นกำลังมีน้ำท่วม

## เริ่มใช้งาน

ต้องใช้ Python 3.11+

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.app.main:app --reload
```

จากนั้นเปิด <http://127.0.0.1:8000>

รันชุดทดสอบ:

```bash
pytest
```

## ตั้งค่า GISTDA

คัดลอก `.env.example` เป็น `.env` แล้วกำหนด URL/API key ตามสิทธิ์ที่ได้รับจาก
GISTDA API Gateway โปรเจกต์จงใจไม่เดา URL ของบริการ เพราะ endpoint และรูปแบบ
authentication อาจต่างกันตามผลิตภัณฑ์/บัญชี

```dotenv
GISTDA_FLOOD_URL=https://your-authorized-endpoint.example/flood
GISTDA_API_KEY=replace-me
GISTDA_API_KEY_HEADER=api-key
```

เมื่อยังไม่ตั้งค่า endpoint `/api/v1/flood/current` จะตอบ GeoJSON ว่างพร้อม
`source_status: "not_configured"` ทำให้ frontend ยังเปิดและใช้ radar/weather/river ได้

## โครงสร้าง

```text
backend/app/
  api/          REST endpoints
  services/     upstream adapters และ TTL cache
  config.py     environment settings
  main.py       FastAPI app และ static hosting
frontend/       Leaflet UI
tests/          backend tests
docs/           บริบทและข้อสรุปที่นำมาจาก ChatGPT
```

## แนวทางต่อยอด

1. ยืนยันและเชื่อม GISTDA flood polygon/WMTS ด้วย credential จริง
2. เก็บ flood geometry ใน PostGIS และ query ตาม viewport
3. simplify geometry ตาม zoom หรือเปลี่ยนเป็น MVT/PMTiles
4. เพิ่ม background refresh และ Redis เมื่อรันหลาย process
5. ~~เพิ่มข้อมูลระดับน้ำจากสถานี~~ (ThaiWater แล้ว) ต่อไป: TMD พยากรณ์ฝน/ประกาศเตือนภัย, MRC น้ำโขง

RainViewer public API ต้องแสดง attribution และเหมาะกับงานส่วนบุคคล การศึกษา
หรือชุมชนขนาดเล็กเท่านั้น หากนำไปใช้เชิงพาณิชย์ควรตรวจเงื่อนไขอีกครั้ง

ข้อมูลสถานีจาก ThaiWater public API ไม่มี license ประกาศชัดเจน โปรเจกต์จึงแสดง
attribution "คลังข้อมูลน้ำแห่งชาติ (สสน.)" และ cache ฝั่ง backend เสมอ ไม่ให้ frontend เรียกตรง
