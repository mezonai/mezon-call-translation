# Mezon Call Dashboard - Tổng quan

## Giới thiệu

Dashboard React hiện đại để quản lý và theo dõi dữ liệu từ các cuộc họp (meeting rooms) của hệ thống Mezon Call Translation.

## Tính năng chính

### 1. 📋 Danh sách Rooms
- Hiển thị toàn bộ meeting rooms với pagination
- Tìm kiếm rooms theo tên
- Hiển thị trạng thái (completed, processing, pending, error)
- Hiển thị số lượng tracks (completed/total)
- Thời gian tạo room
- Refresh data real-time

### 2. 📊 Chi tiết Room

#### Tab Overview
- Thông tin cơ bản room (tên, status, thời gian)
- Statistics cards:
  - Total tracks
  - Completed tracks
  - Total duration
  - Total segments

#### Tab Participants & Transcripts
- **Danh sách participants**: Hiển thị tất cả người tham gia với track information
- **Full transcript**: 
  - Load transcript cho từng participant
  - Hiển thị từng segment với timestamp
  - Độ tin cậy (confidence score) của mỗi segment
  - Scrollable transcript viewer
- **Track details**:
  - Participant identity
  - Track ID & Egress ID
  - Status của từng track

#### Tab Summary
- **Key Points**: Những điểm chính của cuộc họp
- **Action Items**: Danh sách công việc cần làm
- **Decisions**: Các quyết định được đưa ra
- **Participants**: Danh sách người tham gia
- Hiển thị thời gian tạo summary

### 3. 🎨 UI/UX Features
- **Modern Design**: Sử dụng Tailwind CSS
- **Responsive**: Tương thích mọi kích thước màn hình
- **Loading States**: Spinner khi đang load data
- **Error Handling**: Hiển thị lỗi thân thiện với nút retry
- **Interactive**: Hover effects, transitions mượt mà
- **Color-coded Status**: Màu sắc rõ ràng cho từng trạng thái

## Công nghệ sử dụng

| Công nghệ | Version | Mục đích |
|-----------|---------|----------|
| React | 18.2.0 | UI Library |
| Vite | 5.0.8 | Build tool & Dev server |
| React Router | 6.20.0 | Client-side routing |
| Axios | 1.6.2 | HTTP client |
| Tailwind CSS | 3.3.6 | Utility-first CSS |
| PostCSS | 8.4.32 | CSS processing |

## Cấu trúc thư mục

```
dashboard/
├── public/                    # Static assets
│   └── vite.svg
├── src/
│   ├── components/           # React components
│   │   ├── RoomList.jsx     # Danh sách rooms
│   │   ├── RoomDetail.jsx   # Chi tiết room (main feature)
│   │   ├── LoadingSpinner.jsx # Loading component
│   │   └── ErrorMessage.jsx   # Error display
│   ├── services/            # API integration
│   │   └── api.js          # API client & endpoints
│   ├── App.jsx             # Main app with routing
│   ├── main.jsx            # Entry point
│   └── index.css           # Global styles + Tailwind
├── .env                    # Environment variables
├── .env.example           # Environment template
├── .gitignore            # Git ignore rules
├── index.html            # HTML template
├── package.json          # Dependencies
├── vite.config.js       # Vite configuration
├── tailwind.config.js   # Tailwind configuration
├── postcss.config.js    # PostCSS configuration
├── README.md            # Quick start guide
├── SETUP.md             # Detailed setup guide
└── OVERVIEW.md          # This file
```

## API Endpoints sử dụng

### Room Management
```
GET /api/transcripts/rooms
  - Parameters: limit, skip, status
  - Response: { status, total, rooms[] }

GET /api/transcripts/rooms/{room_name}
  - Response: { status, room{} }

GET /api/transcripts/rooms/{room_name}/statistics
  - Response: { status, statistics{} }
```

### Transcripts
```
GET /api/transcripts/tracks/{track_id}/transcript
  - Response: { status, track_id, total_segments, transcript[] }

GET /api/transcripts/tracks/{track_id}/chunks
  - Parameters: limit, skip, sorted_by_index
  - Response: { status, chunks[] }
```

### Summaries
```
GET /api/summary/room/{room_name}
  - Parameters: start_time, end_time (optional)
  - Response: { status, data[], count }
```

## Workflow sử dụng

1. **Khởi động dashboard**: `npm run dev`
2. **Xem danh sách rooms**: Trang chủ hiển thị tất cả rooms
3. **Tìm kiếm**: Gõ tên room vào search box
4. **Xem chi tiết**: Click vào room hoặc "View Details"
5. **Xem overview**: Tab đầu tiên hiển thị thông tin tổng quan
6. **Load transcripts**: 
   - Chuyển sang tab "Participants & Transcripts"
   - Click "Load Transcript" cho từng participant
   - Xem full transcript với timestamps
7. **Xem summary**: Tab "Summary" hiển thị tóm tắt cuộc họp
8. **Quay lại**: Click "← Back" để về danh sách

## Điểm nổi bật

### 1. Real-time Data Loading
- Load data on-demand để tối ưu performance
- Transcript chỉ load khi user click
- Lazy loading cho heavy data

### 2. Error Resilience
- Graceful error handling
- Retry mechanism
- User-friendly error messages
- Network error recovery

### 3. Performance Optimized
- Pagination cho large datasets
- Efficient re-rendering với React hooks
- Optimized API calls
- Conditional loading

### 4. Developer Friendly
- Clean code structure
- Reusable components
- Well-documented
- Easy to extend

## Customization

### Thay đổi màu theme
Edit `tailwind.config.js`:
```javascript
theme: {
  extend: {
    colors: {
      brand: {
        primary: '#your-color',
      }
    }
  }
}
```

### Thêm endpoint mới
Edit `src/services/api.js`:
```javascript
export const newEndpoint = async (params) => {
  const response = await apiClient.get('/your-endpoint');
  return response.data;
};
```

### Thêm component mới
```bash
# Tạo file mới
src/components/YourComponent.jsx

# Import trong App.jsx hoặc component khác
import YourComponent from './components/YourComponent'
```

## Deployment

### Development
```bash
npm run dev
```

### Production Build
```bash
npm run build
# Build output: dist/
```

### Serve Production
```bash
npm run preview
# hoặc deploy thư mục dist/ lên web server
```

## Bảo mật

- API key được lưu trong `.env` (không commit lên git)
- CORS configuration cần được setup ở backend
- Không expose sensitive data
- Input validation

## Testing Tips

1. **Test với backend local**: Đảm bảo orchestrator service chạy
2. **Test API key**: Thử với/không API key
3. **Test error cases**: Tắt backend để test error handling
4. **Test loading states**: Throttle network trong DevTools
5. **Test responsiveness**: Resize browser window

## Future Enhancements

- [ ] Export transcript to PDF/TXT
- [ ] Filter rooms by date range
- [ ] Advanced search với filters
- [ ] Real-time updates với WebSocket
- [ ] Download summary reports
- [ ] User authentication
- [ ] Dark mode
- [ ] Multi-language support
- [ ] Audio playback sync với transcript
- [ ] Collaborative annotations

## Liên hệ & Support

- **Documentation**: Xem file SETUP.md và README.md
- **Issues**: Tạo issue trong repository
- **Development**: Follow coding standards trong project

---

**Version**: 1.0.0  
**Last Updated**: February 2026  
**License**: Theo license của project chính
