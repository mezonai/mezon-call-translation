import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  }
});

// Request interceptor to add JWT token to all requests
apiClient.interceptors.request.use(
  (config) => {
    // Get token from localStorage
    const token = localStorage.getItem('auth_token');

    if (token) {
      config.headers['Authorization'] = `Bearer ${token}`;
    }

    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Response interceptor to handle authentication errors
apiClient.interceptors.response.use(
  (response) => {
    return response;
  },
  (error) => {
    if (error.response?.status === 401) {
      // Token expired or invalid - clear storage and redirect to login
      localStorage.removeItem('auth_token');

      // Only redirect if not already on login/callback pages
      if (!window.location.pathname.startsWith('/login') &&
        !window.location.pathname.startsWith('/callback')) {
        window.location.href = '/login';
      }
    }

    return Promise.reject(error);
  }
);

// Room APIs
export const getRooms = async (params = {}) => {
  const { limit = 20, skip = 0, status = null, search = null, from_utc = null, to_utc = null } = params;
  const queryParams = new URLSearchParams();
  queryParams.set('limit', String(limit));
  queryParams.set('skip', String(skip));
  if (status) queryParams.set('status', status);
  if (search && search.trim()) queryParams.set('search', search.trim());
  if (from_utc) queryParams.set('from_utc', from_utc);
  if (to_utc) queryParams.set('to_utc', to_utc);

  const response = await apiClient.get(`/api/transcripts/rooms?${queryParams.toString()}`);
  return response.data;
};

export const getRoomByName = async (roomName) => {
  const response = await apiClient.get(`/api/transcripts/rooms/${roomName}`);
  return response.data;
};

export const getRoomById = async (roomId) => {
  const response = await apiClient.get(`/api/transcripts/rooms/id/${roomId}`);
  return response.data;
};

export const getRoomStatistics = async (roomName) => {
  const response = await apiClient.get(`/api/transcripts/rooms/${roomName}/statistics`);
  return response.data;
};

export const getRoomStatisticsById = async (roomId) => {
  const response = await apiClient.get(`/api/transcripts/rooms/id/${roomId}/statistics`);
  return response.data;
};

// Summary APIs
export const getSummaryByRoom = async (roomName, startTime = null, endTime = null) => {
  const queryParams = new URLSearchParams();
  if (startTime) queryParams.append('start_time', startTime);
  if (endTime) queryParams.append('end_time', endTime);

  const query = queryParams.toString();
  const url = `/api/summary/room/${roomName}${query ? `?${query}` : ''}`;
  const response = await apiClient.get(url);
  return response.data;
};

export const getSummaryByRoomId = async (roomId) => {
  const response = await apiClient.get(`/api/summary/room/id/${roomId}`);
  return response.data;
};

// Transcript APIs
export const getFullTranscript = async (trackId) => {
  const response = await apiClient.get(`/api/transcripts/tracks/${trackId}/transcript`);
  return response.data;
};

export const getChunksByTrack = async (trackId, params = {}) => {
  const { limit = 100, skip = 0, sorted_by_index = true } = params;
  const queryParams = new URLSearchParams({
    limit,
    skip,
    sorted_by_index
  });

  const response = await apiClient.get(`/api/transcripts/tracks/${trackId}/chunks?${queryParams}`);
  return response.data;
};

export default apiClient;
