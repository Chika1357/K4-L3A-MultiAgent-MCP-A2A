# KHO BẢN KẾ HOẠCH & PHÂN CHIA NHIỆM VỤ TEAM (K4 L3A — MULTI-AGENT MCP + A2A)

Tài liệu này dùng để thống nhất kiến trúc, phân chia công việc độc lập cho 3 thành viên, quy chuẩn giao tiếp (A2A) và các mốc kiểm soát chất lượng từ **Pha 2 đến Pha 5**.

---

## 1. TỔNG QUAN PHÂN VAI & SỞ HỮU TỆP TIN

Nhằm tránh xung đột mã nguồn (Git Merge Conflict) và tối ưu hóa thời gian chạy đua, nhóm phân tách thành 3 trục làm việc độc lập:

| Thành viên | Vai trò chính | Tệp tin sở hữu chính | Trách nhiệm cốt lõi |
| :--- | :--- | :--- | :--- |
| **Người 1** | **Architect & Coordinator Lead** | `ARCHITECTURE.md`<br>`src/student_agent/coordinator.py`<br>`src/student_agent/workflow.py` (ghép luồng) | - Thiết kế kiến trúc tổng thể & cập nhật tài liệu.<br>- Xây dựng Coordinator Agent điều phối, bóc tách ID, chuyển giao task.<br>- Quản lý vòng đời `traces/trace.jsonl`.<br>- Quản lý Git, chạy Batch Run 100 cases & đóng gói bài nộp. |
| **Người 2** | **Specialist Agents & MCP Lead** | `src/student_agent/specialists/`<br>+ `order_agent.py`<br>+ `payment_agent.py`<br>+ `shipment_agent.py` | - Khám phá và kết nối công cụ MCP (`gateway.call`).<br>- Truy vấn dữ liệu có thẩm quyền (ground truth) về đơn hàng, thanh toán, vận chuyển.<br>- Thu thập và chuẩn hóa danh sách `evidence_ref`.<br>- Bắn event `tool_result_consumed` ngay khi nhận dữ liệu. |
| **Người 3** | **Policy & Verifier Lead** | `src/student_agent/policy.py`<br>`src/student_agent/verifier.py`<br>`contracts/schemas/` (đối soát) | - Xây dựng bộ luật trọng tài (phán quyết 11 `primary_issue`).<br>- Tính toán tiền bồi hoàn `recommended_refund_brl` theo đồng BRL.<br>- Kiểm tra tính nhất quán chéo (Cross-field Consistency).<br>- Hiệu chuẩn độ tin cậy (`confidence`).<br>- Bảo đảm 100% Output hợp lệ JSON Schema. |

---

## 2. CẤU TRÚC MÃ NGUỒN PHÂN RÃ (ĐỂ CODE SONG SONG)

Cả nhóm thống nhất chia nhỏ `src/student_agent/` như sau để không ai giẫm chân lên code của nhau:

```text
src/student_agent/
├── workflow.py                 # [Người 1] Điểm vào chính, ráp nối luồng Pipeline tổng thể
├── coordinator.py              # [Người 1] Phân tích case, bóc tách ID, emit case_received & task_assigned
├── specialists/                # [Người 2 phụ trách toàn bộ folder này]
│   ├── __init__.py
│   ├── order_agent.py          # Gọi get_order, get_item, get_product
│   ├── payment_agent.py        # Gọi get_payment, get_refund
│   └── shipment_agent.py       # Gọi get_shipment, đối chiếu mốc thời gian giao hàng
├── policy.py                   # [Người 3] Suy luận Primary Issue, Bên chịu lỗi, Tiền hoàn
└── verifier.py                 # [Người 3] Đối chiếu chéo, tính confidence, validate schema
```

---

## 3. CHI TIẾT NHIỆM VỤ TỪNG THÀNH VIÊN THEO PHA

### 👤 NGƯỜI 1: Architect, Coordinator & Trace Master

#### Pha 2: Thiết kế & Dựng khung Pipeline (30 - 65 phút)
1. **Thiết lập thư mục code:** Tạo cấu trúc thư mục rỗng như trên, đẩy lên branch chung để 2 bạn còn lại pull về.
2. **Hoàn thiện `ARCHITECTURE.md`:** 
   - Điền bảng **Agent ownership**: Xác định rõ Actor (Coordinator, Order, Payment, Shipment, Policy, Verifier), input/output và tool được cấp phép.
   - Điền bảng **Failure policy**: Cơ chế retry (tối đa 2 lần, idempotent) khi MCP timeout hoặc lỗi mạng.
   - Định nghĩa điều kiện handoff, cơ chế correlation qua `case_id`.
3. **Viết `coordinator.py`:**
   - Tiếp nhận `case` thô từ file input.
   - Bóc tách manh mối: `order_id`, `item_id`, `payment_reference`, `shipment_id`,...
   - Ghi nhận event khởi động case vào trace:
     ```python
     trace.emit(case_id=case["case_id"], event_type="case_received", actor="coordinator")
     trace.emit(case_id=case["case_id"], event_type="task_assigned", actor="coordinator", target="specialists")
     ```

#### Pha 3 & 4: Ráp nối Pipeline & Kiểm soát Trace (65 - 150 phút)
1. Ghép nối lời gọi từ `coordinator` $\rightarrow$ `specialists` $\rightarrow$ `policy` $\rightarrow$ `verifier` trong `workflow.py`.
2. Đảm bảo toàn bộ **chuỗi sự kiện Trace bắt buộc** được ghi nhận đúng thứ tự thời gian:
   $$\text{case\_received} \rightarrow \text{task\_assigned} \rightarrow \text{tool\_result\_consumed} \rightarrow \text{handoff} \rightarrow \text{policy\_decided} \rightarrow \text{verification\_completed} \rightarrow \text{case\_finalized}$$
3. Thêm exception handling quanh từng case để khi chạy 100 cases, nếu 1 case lỗi không làm crash toàn bộ batch.

#### Pha 5: Batch Run, Thẩm định & Đóng gói (150 - 210 phút)
1. Tải bộ input ZIP từ GitHub Release, giải nén vào thư mục `inputs/` và file `case-set.json` ở root.
2. Kiểm tra bộ dữ liệu: `day09 validate-inputs`.
3. Thực thi batch run toàn bộ 100 cases: `day09 run`.
4. Thẩm định output và trace: `day09 validate`.
5. Đóng gói bài nộp: `day09 package --output dist/submission.zip` và upload lên Competition Workspace.

---

### 👤 NGƯỜI 2: Specialist Agents & MCP Evidence Collector

#### Pha 2: Thám hiểm MCP Tools (30 - 65 phút)
1. Cấu hình `.env` với Team API Key thật.
2. Chạy lệnh liệt kê các tool khả dụng từ server:
   ```bash
   day09 mcp-tools
   ```
3. Nắm vững định dạng phản hồi chuẩn của MCP Gateway theo `contracts/schemas/mcp-evidence-response-v1.schema.json` (chứa `evidence_ref`, `data`, `domain`).

#### Pha 3: Hiện thực hóa 3 Specialist Agents (65 - 110 phút)
Tất cả các lệnh gọi MCP **bắt buộc** phải truyền đúng `case_id=case["case_id"]` và emit `tool_result_consumed`:

1. **Order / Item Agent (`order_agent.py`):**
   - Gọi `get_order`, `get_item`.
   - Trích xuất: trạng thái đơn hàng (`delivered`, `canceled`, `unavailable`), danh sách item ID, giá niêm yết, seller ID.
   - Emit trace:
     ```python
     trace.emit(case_id=case_id, event_type="tool_result_consumed", actor="order-agent", tool_name="get_order", evidence_refs=[evidence["evidence_ref"]])
     ```
2. **Payment Agent (`payment_agent.py`):**
   - Gọi `get_payment`, `get_refund`.
   - Trích xuất: Tổng tiền khách đã trả, các phương thức thanh toán.
   - Phát hiện các dấu hiệu: Bị trừ tiền 2 lần (`duplicate_charge`), thanh toán chia nhiều lần hợp lệ (`valid_split_payment`), số tiền không khớp (`payment_mismatch`), tiền hoàn đang chờ/bị hủy (`refund_pending`, `refund_failed`).
3. **Shipment Agent (`shipment_agent.py`):**
   - Gọi `get_shipment`.
   - Thu thập mốc thời gian: `shipping_limit_date`, `delivered_carrier_date`, `delivered_customer_date`, `estimated_delivery_date`.
   - Phân định nguyên nhân giao trễ:
     - Giao cho bưu tá sau ngày hạn chót $\rightarrow$ Lỗi do người bán (`late_delivery_seller`).
     - Giao cho bưu tá đúng hạn nhưng tới khách trễ hơn ngày dự kiến $\rightarrow$ Lỗi bên vận chuyển (`late_delivery_logistics`).

#### Pha 4: Chuyển giao dữ liệu sạch (110 - 150 phút)
- Gom toàn bộ facts và danh sách `evidence_refs` (loại bỏ trùng lặp, giữ đúng thứ tự) đóng gói thành một dictionary sạch để bàn giao cho Người 3.

---

### 👤 NGƯỜI 3: Policy Engine, Verifier & Quality Gatekeeper

#### Pha 2: Khóa cứng Schema & Chuẩn hóa cấu trúc Output (30 - 65 phút)
1. Đọc và làm chủ schema `contracts/schemas/l3a-output-v2.schema.json`.
2. Tạo khung dữ liệu Python (Dictionary hoặc Pydantic class) khóa cứng tất cả các Enum:
   - 11 `primary_issue`: `canceled_order_paid`, `unavailable_order_paid`, `late_delivery_seller`, `late_delivery_logistics`, `valid_split_payment`, `payment_mismatch`, `duplicate_charge`, `refund_pending`, `refund_failed`, `unsupported_claim`, `insufficient_evidence`.
   - `case_status`: `action_required`, `no_action`, `needs_investigation`.
   - `party_type`: `seller`, `platform`, `logistics_provider`, `payment_provider`, `customer`, `unknown`.
   - `currency`: Cố định `"BRL"`.

#### Pha 3 & 4: Bộ luật Phán quyết & Bộ kiểm định (65 - 150 phút)
1. **Triển khai `policy.py` (Trọng tài tranh chấp - Chiếm 45% điểm Semantic):**
   - Dựa vào facts do Người 2 cung cấp để suy luận:
     - **Xác định Primary Issue:** So khớp tình huống thực tế với 11 mã sự cố chuẩn.
     - **Quy trách nhiệm (`responsible_parties`):** Nếu lỗi người bán $\rightarrow$ party_type là `seller` (kèm `seller_id`), nếu lỗi vận chuyển $\rightarrow$ `logistics_provider`, nếu lỗi cổng thanh toán $\rightarrow$ `payment_provider`.
     - **Giải pháp tài chính (`financial_resolution`):**
       - Tính chính xác `recommended_refund_brl` (tổng tiền các dòng `refund_lines`).
       - *Nguyên tắc sống còn:* Nếu `case_status == "no_action"` thì `recommended_refund_brl` phải bằng `0.0`.
     - **Hành động xử lý (`resolution_actions`):** Mảng các chuỗi hành động cụ thể (tối đa 8 actions, unique).
     - **Xử lý mâu thuẫn (`data_conflicts`):** Ghi nhận sai lệch giữa lời nói của khách hàng và dữ liệu MCP thực tế.
   - Ghi trace:
     ```python
     trace.emit(case_id=case_id, event_type="policy_decided", actor="policy-agent", decision_code=primary_issue)
     ```
2. **Triển khai `verifier.py` (Chốt chặn an toàn cuối cùng):**
   - **Cross-field Consistency (10% điểm):**
     - Đảm bảo tính logic giữa `primary_issue`, `case_status`, `responsible_parties` và `financial_resolution`.
     - Lỗi thuộc bên nào thì bên đó phải có tên trong `responsible_parties`.
   - **Confidence Calibration (5% điểm):**
     - Đầy đủ chứng cứ, không conflict: set `0.85 - 0.95`.
     - Có conflict nhẹ nhưng giải quyết được: `0.70 - 0.84`.
     - Dữ liệu thiếu/nghi vấn: `0.40 - 0.60`.
     - *Tuyệt đối không set cứng 1.0 cho tất cả các case.*
   - **Schema Gate:** Dùng `contracts.validate_output(output, ...)` kiểm tra trước khi trả về. Nếu có lỗi phải sửa ngay lập tức tại đây!
   - Ghi trace:
     ```python
     trace.emit(case_id=case_id, event_type="verification_completed", actor="verifier", decision_code="PASS")
     ```

---

## 4. CÁC QUY TẮC "SỐNG CÒN" (TRÁNH BỊ 0 ĐIỂM / HARD GATES)

| # | Quy tắc | Lý do / Nguy cơ |
| :---: | :--- | :--- |
| **1** | **KHÔNG tự bịa `evidence_ref`** | Server audit chéo 100%. Nếu có 1 ref giả mạo $\rightarrow$ **0 điểm toàn bộ case** (*Hard Gate*). |
| **2** | **KHÔNG dùng chéo evidence giữa các case** | Mỗi `evidence_ref` gắn chặt với `case_id` lúc gọi MCP. Dùng chéo $\rightarrow$ Vi phạm bảo mật, 0 điểm. |
| **3** | **KHÔNG thêm field ngoài schema** | Schema có `"additionalProperties": false`. Bất kỳ field lạ nào lọt vào output $\rightarrow$ Lỗi schema $\rightarrow$ 0 điểm. |
| **4** | **Bảo mật bí mật API Key** | `submission.py` sẽ quét toàn bộ output và trace. Nếu để lọt chuỗi `sk-team-...` vào file $\rightarrow$ **Bị từ chối đóng gói**. |
| **5** | **Customer message không phải Ground Truth** | Khách nói đơn bị mất hay trừ tiền oan có thể là nhầm lẫn. Luôn căn cứ vào dữ liệu MCP. |

---

## 5. MỐC THỜI GIAN ĐỒNG BỘ (CHECKPOINTS)

* **Checkpoint 1 (Phút 65 - Hết Pha 2):**
  - Người 1 đẩy cấu trúc folder và `ARCHITECTURE.md` sơ thảo.
  - Người 2 chạy thành công `day09 mcp-tools` xác nhận kết nối MCP.
  - Người 3 tạo xong template output dict rỗng vượt qua `contracts.validate_output`.
* **Checkpoint 2 (Phút 110 - Hết Pha 3):**
  - Người 2 hoàn thành 3 specialist, test gọi MCP thành công và emit đúng `tool_result_consumed` trên 1 case mẫu.
* **Checkpoint 3 (Phút 150 - Hết Pha 4):**
  - Người 3 hoàn thành logic phân xử `policy.py` và kiểm định `verifier.py`.
  - Người 1 ráp toàn bộ vào `workflow.py`, chạy thử 1 case đơn lẻ đạt chuẩn 100% Schema và Trace.
* **Checkpoint 4 (Phút 150 - 210 - Pha 5):**
  - Tải 100 cases inputs, chạy `day09 run`.
  - Cả 3 cùng theo dõi log, nếu case nào fail schema thì Người 3 fix, case nào lỗi gọi tool thì Người 2 fix.
  - Chạy `day09 validate` thành công $\rightarrow$ Đóng gói `day09 package` $\rightarrow$ Nộp bài.

---

## 6. SỔ TAY CÁC LỆNH CLI CẦN NHỚ

```bash
# 1. Xem danh sách công cụ MCP đang mở
day09 mcp-tools

# 2. Kiểm tra tính toàn vẹn của 100 file input
day09 validate-inputs

# 3. Chạy toàn bộ 100 cases
day09 run

# 4. Kiểm tra tính hợp lệ của outputs và trace
day09 validate

# 5. Đóng gói file nộp bài (dist/submission.zip)
day09 package --output dist/submission.zip
```
