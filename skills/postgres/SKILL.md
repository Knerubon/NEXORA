---
name: postgres
description: ออกแบบ normalized event/derived state persistence และ migrations ใน tasks ที่ระบุ PostgreSQL
---

# postgres

เก็บ raw/normalized provenance, effective config/version และ run identity ให้ replay ได้
- schema ต้องระบุ event ordering/dedup keys, precision, timezone และ transaction boundaries
- เก็บ raw data แบบ immutable; correction/derived rebuild ใช้ explicit version/run
- migrations ต้องมี upgrade/rollback หรือ recovery plan; idempotent ingest ไม่ overwrite ต่าง payload แบบเงียบ ๆ
- ทดสอบ duplicate, interrupted transaction, restart และ reproducible query order
- DB อยู่ private; ใช้ runtime secrets ไม่มี credential literals ใน examples
ส่ง migration/schema notes + integration evidence; ไม่จำเป็นต้องอ่าน production data
