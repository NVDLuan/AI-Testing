2026-04-20 - Added timezone field to all cameras
## 2026-04-09 - Initial
- Initial config for CNT09 (UNDERRATIO rule)
- Copied from CNT08 config (same cameras, same ratios)
- Logic: teacher_count > required_teachers → UNDERRATIO violation
- required_teachers = ceil(student_count * default_teacher_count / default_student_count)
