# Mezon Call Dashboard

Dashboard React hiện đại để quản lý và xem dữ liệu từ các cuộc họp (meeting rooms).

## 🚀 Quick Start

### 1. Cài đặt

```bash
cd Architect_MultiClient_Server/dashboard
npm install
```

### 2. Cấu hình (Optional)

File `.env` đã được tạo sẵn với cấu hình mặc định. Nếu cần thay đổi:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_API_KEY=your_api_key_here
```

### 3. Chạy

```bash
npm run dev
```

Dashboard sẽ chạy tại: **http://localhost:3000**

## ✨ Tính năng

### 📋 Danh sách Rooms
- Hiển thị tất cả meeting rooms với pagination
- Tìm kiếm rooms theo tên
- Hiển thị trạng thái và số lượng tracks
- Refresh data real-time

### 📊 Chi tiết Room

**Tab Overview:**
- Thông tin cơ bản về room
- Thống kê: tracks, duration, segments

**Tab Participants & Transcripts:**
- Danh sách participants với track info
- **Load full transcript** cho từng participant
- Hiển thị segment với timestamp và confidence score
- Scrollable transcript viewer

**Tab Summary:**
- Key points của meeting
- Action items
- Decisions
- Danh sách participants

## 🛠 Công nghệ

- **React 18** - UI Library
- **Vite** - Build tool
- **React Router** - Routing
- **Axios** - HTTP client
- **Tailwind CSS** - Styling

## 📖 Documentation

- **[SETUP.md](./SETUP.md)** - Hướng dẫn setup chi tiết
- **[OVERVIEW.md](./OVERVIEW.md)** - Tổng quan về project

## 🎯 Build Production

```bash
npm run build    # Build
npm run preview  # Preview build
```

## 🔧 Troubleshooting

### Lỗi CORS
Thêm CORS middleware vào backend (orchestrator service):

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Backend không kết nối được
1. Kiểm tra orchestrator service đang chạy tại port 8000
2. Kiểm tra `.env` file có đúng URL
3. Kiểm tra API key (nếu backend yêu cầu)

## 📸 Screenshots

### Danh sách Rooms
- Table view với pagination
- Status badges
- Search functionality

### Chi tiết Room
- Multi-tab interface
- Statistics cards
- Interactive transcript viewer
- Summary display

## 🤝 Contributing

Xem file [OVERVIEW.md](./OVERVIEW.md) để hiểu cấu trúc project và cách thêm tính năng mới.

---

**Version**: 1.0.0  
**License**: Theo license của project chính
