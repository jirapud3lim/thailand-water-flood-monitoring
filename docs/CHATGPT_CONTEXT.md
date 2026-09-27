# บริบทที่นำมาจาก ChatGPT

นำเข้าจากบทสนทนา “สร้างแผนที่น้ำท่วมไทย” และ “API น้ำท่วมประเทศไทย”
เมื่อ 27 กันยายน 2026 เพื่อใช้เป็นจุดตั้งต้นของ workspace นี้

## เป้าหมาย

สร้างระบบติดตามน้ำท่วมประเทศไทยแบบ interactive GIS ซึ่งรวมข้อมูลหลายประเภท:

- flood extent/polygon จาก GISTDA
- radar จาก RainViewer
- rainfall จาก Open-Meteo และพิจารณา TMD/ThaiWater ในอนาคต
- river discharge จาก GloFAS ผ่าน Open-Meteo Flood API
- รองรับ alert, historical playback และ risk score ในระยะถัดไป

## การตัดสินใจด้านสถาปัตยกรรม

- ย้ายจาก standalone HTML เป็น frontend/backend แยกกัน
- เริ่มแบบเล็กด้วย Leaflet + FastAPI แล้วค่อยย้ายไป MapLibre เมื่อข้อมูลใหญ่จริง
- ให้ backend จัดการ cache, retry, API key, normalization และ filtering
- โหลด flood data ตาม viewport/bounding box แทนการดาวน์โหลดทั้งประเทศ
- ไม่ refresh ทุก layer พร้อมกัน: radar 5 นาที, rainfall 10 นาที, river ช้ากว่า
- ใช้ stale-while-revalidate/fallback เพื่อให้ข้อมูลเดิมยังแสดงเมื่อ upstream สะดุด
- เป้าหมาย production คือ PostgreSQL/PostGIS + Redis + vector tile/PMTiles

## Performance requirements

ปัญหาเดิมคือ browser โหลดและ render GeoJSON ขนาดใหญ่ทั้งชุดจนแผนที่ค้าง แนวทางที่เลือก:

1. viewport-based loading
2. spatial chunks หรือ tiles
3. geometry simplification ตาม zoom
4. incremental render และ Canvas renderer
5. cache และ version check ก่อนโหลด flood polygon ใหม่
6. ย้าย preprocessing ออกจาก browser ไปไว้ที่ backend/worker

## API surface ที่วางไว้

```text
GET /api/v1/health
GET /api/v1/radar/latest
GET /api/v1/weather/current?lat=...&lon=...
GET /api/v1/river?lat=...&lon=...
GET /api/v1/flood/current?min_lon=...&min_lat=...&max_lon=...&max_lat=...
```

## ข้อควรระวังด้านข้อมูล

- flood extent จากดาวเทียมไม่เท่ากับระดับน้ำขังบนถนนเป็นเซนติเมตร
- river discharge ความละเอียดประมาณ 5 กม. เป็นสัญญาณจากแบบจำลอง ไม่ใช่หลักฐานยืนยันน้ำท่วม
- endpoint และ authentication ของ GISTDA ต้องยืนยันจากบัญชี/เอกสารที่ได้รับสิทธิ์ก่อนใช้งาน
- RainViewer public API มีข้อกำหนด attribution และข้อจำกัดด้านการใช้งาน/zoom
- dashboard ต้องแสดงเวลาอัปเดต แหล่งข้อมูล และสถานะ stale/offline ให้ชัดเจน

## Phase ที่วางไว้

- Phase 1: FastAPI facade + Leaflet UI + cache + online radar/weather/river
- Phase 2: GISTDA integration + PostGIS + bbox query + geometry simplification
- Phase 3: MapLibre + MVT/PMTiles + workers + Redis + WebSocket + alert/history

## ส่วนขยายวันที่ 27 กันยายน 2026

- เพิ่มเขื่อนขนาดใหญ่ โดยใช้ค่าปริมาณน้ำรายวันจากกรมชลประทานและพิกัดจาก ปภ.
- เพิ่มสถานีระดับน้ำทั่วประเทศ คำนวณระดับสีจากระยะระดับน้ำถึงตลิ่ง
- เพิ่มจุดวัดน้ำท่วมถนนกรุงเทพฯ ซึ่งมีค่าความลึกน้ำขังเป็นเซนติเมตร
- frontend แยกประเภทข้อมูลใน popup อย่างชัดเจน ไม่เรียกสถานีระดับน้ำว่าเป็นค่าความลึกน้ำท่วมถนน
