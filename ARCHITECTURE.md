# L3A Architecture Record

Tài liệu mô tả quyết định có thể kiểm chứng của hệ thống; không chứa prompt hay chain-of-thought.
Toàn bộ workflow là **Python async state-machine thuần**, deterministic, không dùng LLM.

## 1. System overview

```text
inputs/<case_id>.json
      │  case_received (cli)
      ▼
┌─────────────┐ task_assigned  ┌──────────────┐  MCP: get_order, get_order_items
│ Coordinator │──────────────▶│ order-agent  │──────────────────────────────────────────────┐
│ (workflow.py│◀──────────────│              │  handoff(facts_ready, evidence_refs)          │
│  solve_case)│               └──────────────┘                                               │
│             │ task_assigned  ┌──────────────┐  MCP: get_order_payments,                    │
│             │──────────────▶│payment-agent │       get_payment_timeline,                   │  tool_result_consumed
│             │◀──────────────│              │       get_refund_timeline                    ├─▶ traces/trace.jsonl
│             │ task_assigned  ┌──────────────┐  MCP: get_shipment_summary                   │
│             │──────────────▶│shipment-agent│                                               │
│             │◀──────────────└──────────────┘                                               │
│             │ task_assigned  ┌──────────────┐  MCP: get_policy                             │
│             │──────────────▶│ policy-agent │── policy_decided ─────────────────────────────┘
└─────────────┘               └──────┬───────┘
      ▲                              │ handoff(verify_decision)
      │ handoff(verified)     ┌──────▼───────┐
      └───────────────────────│verifier-agent│── verification_completed
                              └──────────────┘
      │  output → outputs/<case_id>.json, case_finalized (cli)
```

Module chính trong `src/student_agent/`:

- `cli.py`: lệnh `day09`, chạy case, validate artifact và đóng gói submission.
- `cases.py`, `contracts.py`, `config.py`: đọc input, schema public contract và cấu hình `.env`.
- `mcp_gateway.py`: kết nối MCP Evidence Gateway, luôn truyền `case_id` vào payload tool.
- `specialists/`: collector và các specialist `order-agent`, `payment-agent`, `shipment-agent`.
- `workflow.py`: coordinator deterministic, phân tích facts, chọn evidence, gọi verifier.
- `policy.py`: fallback policy local cho unit test và logic tham chiếu.
- `verifier.py`: chuẩn hóa output cuối, chặn inconsistency trước khi ghi.
- `trace.py`: ghi trace event theo `trace-event-v1`.
- `submission.py`: validate artifact, build manifest và tạo ZIP đúng contract.

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Output/handoff |
| --- | --- | --- | --- |
| Coordinator | Case input (chỉ là gợi ý: `order_id` hoặc id Olist 32-hex trong message) | Lập context, giao việc, gộp report, ráp output | `task_assigned` tới từng agent; output cuối |
| Order/item (`order-agent`) | `order_id` | Đọc order, item, seller; xác định trạng thái, mốc thời gian, tổng tiền, seller/product id | `handoff facts_ready` + evidence refs |
| Payment (`payment-agent`) | `order_id` đã xác thực | Các payment, timeline thu tiền, timeline hoàn tiền; tính `paid_total`, `refunded_total`, `refund_state` | `handoff facts_ready` |
| Shipment (`shipment-agent`) | `order_id` đã xác thực | Mốc bàn giao cho hãng vận chuyển, giao hàng, dự kiến, shipment id, hãng vận chuyển | `handoff facts_ready` |
| Policy (`policy-agent`) | Facts của 3 specialist + policy | Chọn `primary_issue`, `case_status`, bên chịu trách nhiệm, refund lines, actions, evidence cần trích dẫn | `policy_decided` + `handoff verify_decision` |
| Verifier (`verifier-agent`) | Output nháp + sổ evidence của case | Kiểm tra chéo và sửa lỗi nhất quán, loại evidence ngoài phạm vi | `verification_completed` + `handoff verified` |

Quyền gọi tool (được enforce trong `Specialist.fetch`, gọi tool ngoài danh sách sẽ ném `PermissionError`):

| Agent | Tools được phép |
| --- | --- |
| order-agent | `get_order`, `get_order_items` |
| payment-agent | `get_order_payments`, `get_payment_timeline`, `get_refund_timeline` |
| shipment-agent | `get_shipment_summary` |
| policy-agent | `get_policy` |
| coordinator, verifier | không gọi MCP |

`get_customer_history` không được cấp cho agent nào: dữ liệu khách hàng không cần cho kết luận và
có rủi ro bị trừ điểm do trích dẫn domain không liên quan.

Tham số tool được lấy từ `inputSchema` mà server công bố (tool discovery). Nếu thiếu một tham số
bắt buộc thì agent **bỏ qua lời gọi**, không đoán id. `get_sellers` và `get_product_context` chỉ
được thêm vào workflow khi server trả ổn định và dữ liệu đó thực sự được cite trong output.

## 3. A2A protocol

- Envelope `Message{message_id, case_id, sender, recipient, intent, payload, evidence_refs}`.
- `Bus` được tạo riêng cho mỗi case và từ chối message mang `case_id` khác.
- `assign()` emit `task_assigned` (actor = người giao, target = người nhận, decision_code = intent).
  `handoff()` emit `handoff` kèm evidence refs mà bên gửi đang chuyển giao.
- Điều kiện handoff: specialist trả `facts_ready` nếu có evidence, ngược lại trả `facts_missing`.
  Policy chỉ chạy sau khi đủ 3 report. Verifier chỉ chạy sau `policy_decided`.
- Chống vòng lặp: luồng là DAG cố định, và `MAX_HOPS = 12` message cho mỗi case.
- Timeout: mỗi lời gọi MCP bị giới hạn `CALL_TIMEOUT_S = 60s`.
- Trace chỉ chứa event, intent, decision code và số đếm, không chứa suy luận.

## 4. Evidence lifecycle

1. `EvidenceGateway.call` luôn truyền `case_id` và validate envelope theo `mcp-evidence-response-v1`.
2. Agent bọc kết quả thành `Evidence{case_id, tool, ref, domain, data}`. `evidence_ref` được giữ
   nguyên văn, không sửa, không tự sinh.
3. Mỗi evidence dùng được sẽ emit ngay `tool_result_consumed` (actor = agent, tool_name, evidence_refs).
4. Policy chọn `cite_tools` theo loại issue. Output chỉ trích dẫn ref của các tool đó, không phải toàn bộ ref.
5. Verifier giữ lại chỉ những ref có trong sổ evidence của **chính case đó và run đó**.
   Sổ này bị huỷ khi case kết thúc nên không thể tái sử dụng evidence giữa các case.

## 5. Failure policy

| Failure | Retry? | Fallback | Trace event/code |
| --- | --- | --- | --- |
| MCP timeout / lỗi transport | Có, tối đa 2 lần, backoff 0.5s và 1s (tool chỉ đọc nên idempotent) | Bỏ tool đó; nếu thiếu `get_order` thì `insufficient_evidence` | `handoff facts_missing`, failure `TIMEOUT` |
| Not found / forbidden (tool error) | Không | Không dùng dữ liệu đó, không suy đoán | failure `NOT_FOUND` / `TOOL_ERROR` |
| Source conflict (vd. ngày giao giữa order và shipment lệch hơn 24h) | Không | Ưu tiên nguồn chuyên trách (shipment cho thời gian, line items cho tổng tiền), ghi vào `data_conflicts`, giảm confidence | `verification_completed` attribute `confidence` |
| Bản ghi ngoài vòng đời đơn hàng (item có `shipping_limit` ngoài [mua, hẹn giao], payment/refund event ngoài [mua, `opened_at`]) | Không | Loại bỏ trước khi tính toán, không trích dẫn làm căn cứ | fact `excluded_*` |
| Invalid specialist result / exception | Không | Report rỗng với `SPECIALIST_FAILED`, case vẫn chạy tiếp | `handoff facts_missing` |

## 6. Verification invariants

Verifier kiểm tra các điều sau trước khi finalize:

- Evidence ownership: mọi ref phải thuộc case/run hiện tại và đúng pattern `ev_…`.
- Money: `recommended_refund_brl` = tổng `refund_lines`, không có line bằng 0.
- Status/refund/action: `no_action` thì refund = 0. Refund > 0 thì status là `action_required`.
  Không có action hoàn tiền khi refund = 0.
- Responsibility: party loại `seller` phải là seller của đơn hàng này.
- Cause code đúng pattern, rank liên tục từ 1. Các entity list không trùng lặp, tối đa 20 phần tử.
- Confidence: kẹp trong [0.05, 0.95]. Không có evidence thì ≤ 0.3. Có conflict thì ≤ 0.8.
- Schema: `cli run` validate output theo `l3a-output-v2` trước khi ghi file.

## 7. Reproducibility

- Không dùng LLM, không có random seed. Cùng dữ liệu MCP sẽ cho cùng output.
- Dependency theo `pyproject.toml` (`mcp>=2,<3`, `httpx2>=2,<3`, `jsonschema>=4.25`), Python 3.11.
- Concurrency: mặc định chạy tuần tự; khi cần benchmark nhanh dùng `day09 run --workers 4`.
  Mỗi worker xử lý một case riêng, sau đó CLI gộp trace theo thứ tự case để dễ audit.
- Lệnh chạy submit chuẩn: `day09 validate-inputs`,
  `day09 run --artifacts-root dist/run-artifacts --workers 4`,
  `day09 validate --artifacts-root dist/run-artifacts`,
  `day09 package --artifacts-root dist/run-artifacts --output dist/submission.zip`.
- Ngưỡng tính toán: `MONEY_TOL = 0.05 BRL`, `LATE_TOL_HOURS = 24`, `CONFLICT_TOL_HOURS = 24`.
- API key chỉ nằm trong `.env`. `day09 package` kiểm tra secret pattern trong output/trace và
  kiểm tra ZIP cuối chỉ chứa `manifest.json`, `trace.jsonl`, `outputs/<case_id>.json`.
