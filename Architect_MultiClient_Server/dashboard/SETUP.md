# Hướng dẫn Setup Dashboard

## Yêu cầu

- Node.js >= 16.0.0
- npm hoặc yarn
- Backend orchestrator service đang chạy tại port 8000

## Bước 1: Cài đặt dependencies

```bash
cd Architect_MultiClient_Server/dashboard
npm install
```

## Bước 2: Cấu hình môi trường

File `.env` đã được tạo sẵn với cấu hình mặc định:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_API_KEY=
```

**Chú ý:** Nếu backend API yêu cầu API key, hãy thêm vào `VITE_API_KEY`.

## Bước 3: Chạy Development Server

```bash
npm run dev
```

Dashboard sẽ chạy tại: `http://localhost:3000`

## Bước 4: Build Production (Optional)

```bash
npm run build
```

File build sẽ được tạo trong thư mục `dist/`

Để xem preview production build:

```bash
npm run preview
```

## Cấu hình API Backend

Dashboard kết nối với các API endpoints sau:

### Room APIs
- `GET /api/transcripts/rooms` - Lấy danh sách rooms
- `GET /api/transcripts/rooms/{room_name}` - Lấy chi tiết room
- `GET /api/transcripts/rooms/{room_name}/statistics` - Lấy thống kê room

### Transcript APIs
- `GET /api/transcripts/tracks/{track_id}/transcript` - Lấy full transcript

### Summary APIs
- `GET /api/summary/room/{room_name}` - Lấy summary của room

## Cấu hình API Key

Nếu backend yêu cầu xác thực, có 2 cách:

### Cách 1: Sử dụng .env file
```env
VITE_API_KEY=your_api_key_here
```

### Cách 2: Sửa file `src/services/api.js`
```javascript
const API_KEY = 'your_api_key_here';
```

## Tính năng Dashboard

### 1. Danh sách Rooms
- Hiển thị tất cả meeting rooms
- Phân trang
- Tìm kiếm theo tên room
- Hiển thị trạng thái và số lượng tracks

### 2. Chi tiết Room
**Tab Overview:**
- Thông tin cơ bản về room
- Thời gian tạo, hoàn thành
- Số lượng tracks

**Tab Participants & Transcripts:**
- Danh sách participants
- Load và hiển thị full transcript cho từng participant
- Thời gian và độ tin cậy của từng segment

**Tab Summary:**
- Key points của meeting
- Action items
- Decisions
- Danh sách participants

## Troubleshooting

### Lỗi CORS
Nếu gặp lỗi CORS, thêm configuration vào backend:

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

### Lỗi kết nối API
1. Kiểm tra backend đang chạy tại `http://localhost:8000`
2. Kiểm tra file `.env` có đúng URL không
3. Kiểm tra API key (nếu cần)

### Port 3000 đã được sử dụng
Sửa file `vite.config.js`:

```javascript
export default defineConfig({
  server: {
    port: 3001, // Đổi sang port khác
    // ...
  }
})
```

## Stack Công nghệ

- **React 18** - UI Library
- **Vite** - Build tool
- **React Router** - Routing
- **Axios** - HTTP client
- **Tailwind CSS** - Styling
- **PostCSS** - CSS processing

## Cấu trúc Project

```
dashboard/
├── public/               # Static files
├── src/
│   ├── components/      # React components
│   │   ├── RoomList.jsx
│   │   ├── RoomDetail.jsx
│   │   ├── LoadingSpinner.jsx
│   │   └── ErrorMessage.jsx
│   ├── services/        # API services
│   │   └── api.js
│   ├── App.jsx          # Main app component
│   ├── main.jsx         # Entry point
│   └── index.css        # Global styles
├── .env                 # Environment variables
├── package.json
├── vite.config.js
└── tailwind.config.js
```

## Phát triển thêm

### Thêm feature mới

1. Tạo component mới trong `src/components/`
2. Thêm API service trong `src/services/api.js`
3. Thêm route trong `src/App.jsx` (nếu cần)

### Customize styling

Sửa file `tailwind.config.js` để thay đổi theme:

```javascript
export default {
  theme: {
    extend: {
      colors: {
        primary: '#your-color',
      },
    },
  },
}
```

## Support

Nếu gặp vấn đề, vui lòng tạo issue hoặc liên hệ team phát triển.
