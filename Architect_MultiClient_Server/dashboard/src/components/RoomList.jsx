import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { getRooms } from '../services/api';
import { formatDate } from '../utils/datetime';
import { getStatusBadge } from '../utils/display';
import { ROOM_STATUS_FILTER_OPTIONS } from '../constants/roomStatus';
import { TIME_RANGE_PRESET, TIME_RANGE_PRESET_OPTIONS, getTimeRangeForPreset } from '../constants/timeRange';

const SEARCH_DEBOUNCE_MS = 500;

const RefreshIcon = () => (
  <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
  </svg>
);

/** Shared modern dropdown style */
const selectClass =
  'pl-4 pr-10 py-2.5 text-sm text-gray-700 bg-white border border-gray-200 rounded-xl shadow-sm ' +
  'focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 focus:outline-none ' +
  'hover:border-gray-300 transition-colors min-w-0 appearance-none bg-no-repeat bg-[length:1.25rem_1.25rem] bg-[right_0.75rem_center] cursor-pointer';

const SKELETON_ROWS = 10;

function TableSkeleton() {
  return (
    <tbody className="bg-white divide-y divide-gray-200">
      {Array.from({ length: SKELETON_ROWS }).map((_, i) => (
        <tr key={i} className="animate-pulse">
          <td className="px-6 py-4 whitespace-nowrap">
            <div className="h-4 bg-gray-200 rounded w-48 max-w-full" />
          </td>
          <td className="px-6 py-4 whitespace-nowrap">
            <div className="h-5 bg-gray-200 rounded-full w-20" />
          </td>
          <td className="px-6 py-4 whitespace-nowrap">
            <div className="h-4 bg-gray-200 rounded w-28" />
          </td>
          <td className="px-6 py-4 whitespace-nowrap">
            <div className="h-4 bg-gray-200 rounded w-28" />
          </td>
          <td className="px-6 py-4 whitespace-nowrap">
            <div className="h-4 bg-gray-200 rounded w-24" />
          </td>
        </tr>
      ))}
    </tbody>
  );
}

const RoomList = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const pageFromUrl = Math.max(0, parseInt(searchParams.get('page') || '0', 10) || 0);

  const [rooms, setRooms] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [currentPage, setCurrentPage] = useState(() => pageFromUrl);
  const [totalRooms, setTotalRooms] = useState(0);
  const [searchTerm, setSearchTerm] = useState(() => searchParams.get('search') || '');
  const [searchDebounced, setSearchDebounced] = useState(() => searchParams.get('search') || '');
  const [statusFilter, setStatusFilter] = useState(() => searchParams.get('status') || '');
  const [timePreset, setTimePreset] = useState(() => searchParams.get('time_preset') || TIME_RANGE_PRESET.NONE);
  const debounceRef = useRef(null);
  const appliedSearchRef = useRef(searchParams.get('search') || '');
  const navigate = useNavigate();

  const ITEMS_PER_PAGE = 20;

  // Debounce search → update searchDebounced after SEARCH_DEBOUNCE_MS; reset page only when applied search changes
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      const next = searchTerm.trim();
      const prev = appliedSearchRef.current;
      appliedSearchRef.current = next;
      setSearchDebounced(next);
      if (next !== prev) setCurrentPage(0);
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchTerm]);

  const getFromToUtc = useCallback(() => getTimeRangeForPreset(timePreset), [timePreset]);

  // Keep URL in sync with page, status, search (debounced), time range
  useEffect(() => {
    const params = new URLSearchParams(searchParams);
    if (currentPage > 0) params.set('page', String(currentPage));
    else params.delete('page');
    if (statusFilter) params.set('status', statusFilter);
    else params.delete('status');
    if (searchDebounced.trim()) params.set('search', searchDebounced.trim());
    else params.delete('search');
    if (timePreset && timePreset !== TIME_RANGE_PRESET.NONE) {
      params.set('time_preset', timePreset);
      const { fromUtc, toUtc } = getFromToUtc();
      if (fromUtc) params.set('from_utc', fromUtc);
      if (toUtc) params.set('to_utc', toUtc);
    } else {
      params.delete('time_preset');
      params.delete('from_utc');
      params.delete('to_utc');
    }
    setSearchParams(params, { replace: true });
  }, [currentPage, statusFilter, searchDebounced, timePreset, getFromToUtc]);

  useEffect(() => {
    fetchRooms();
  }, [currentPage, statusFilter, searchDebounced, timePreset]);

  const fetchRooms = async () => {
    try {
      setLoading(true);
      setError(null);
      const { fromUtc, toUtc } = getFromToUtc();
      const data = await getRooms({
        limit: ITEMS_PER_PAGE,
        skip: currentPage * ITEMS_PER_PAGE,
        ...(statusFilter && { status: statusFilter }),
        ...(searchDebounced.trim() && { search: searchDebounced.trim() }),
        ...(fromUtc && { from_utc: fromUtc }),
        ...(toUtc && { to_utc: toUtc })
      });
      setRooms(data.rooms || []);
      setTotalRooms(data.total || 0);
    } catch (err) {
      setError(err.message || 'Failed to fetch rooms');
      console.error('Error fetching rooms:', err);
    } finally {
      setLoading(false);
    }
  };

  const totalPages = Math.ceil(totalRooms / ITEMS_PER_PAGE);

  const goToRoom = (roomId) => {
    const params = new URLSearchParams();
    if (currentPage > 0) params.set('from_page', String(currentPage));
    if (statusFilter) params.set('status', statusFilter);
    if (searchDebounced.trim()) params.set('search', searchDebounced.trim());
    if (timePreset && timePreset !== TIME_RANGE_PRESET.NONE) {
      params.set('time_preset', timePreset);
      const { fromUtc, toUtc } = getFromToUtc();
      if (fromUtc) params.set('from_utc', fromUtc);
      if (toUtc) params.set('to_utc', toUtc);
    }
    const query = params.toString();
    navigate(`/room/${roomId}${query ? `?${query}` : ''}`);
  };

  if (error) {
    return (
      <div>
        <div className="mb-6">
          <h2 className="text-2xl font-bold text-gray-900 mb-4">Meeting Rooms</h2>
          <div className="flex gap-4 items-center">
            <div className="flex-1 flex gap-3">
              <input
                type="text"
                placeholder="Search by room name or participant..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="flex-1 px-4 py-2.5 text-sm border border-gray-200 rounded-xl shadow-sm focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 focus:outline-none hover:border-gray-300 transition-colors"
              />
              <select value={statusFilter} onChange={() => {}} className={`${selectClass} min-w-[140px]`} style={{ backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")` }}>
                {ROOM_STATUS_FILTER_OPTIONS.map((opt) => (<option key={opt.value || 'all'} value={opt.value}>{opt.label}</option>))}
              </select>
              <select value={timePreset} onChange={() => {}} className={`${selectClass} min-w-[160px]`} style={{ backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")` }}>
                {TIME_RANGE_PRESET_OPTIONS.map((opt) => (<option key={opt.value} value={opt.value}>{opt.label}</option>))}
              </select>
            </div>
            <button onClick={fetchRooms} className="px-4 py-2.5 bg-blue-600 text-white text-sm font-medium rounded-xl shadow-sm hover:bg-blue-700 hover:shadow transition inline-flex items-center">
              <RefreshIcon /> Refresh
            </button>
          </div>
        </div>
        <div className="bg-red-50 border border-red-200 rounded-xl p-4">
          <p className="text-red-800">Error: {error}</p>
          <button onClick={fetchRooms} className="mt-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700">
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-gray-900 mb-4">Meeting Rooms</h2>
        
        <div className="flex gap-4 items-center">
          <div className="flex-1 flex gap-3">
            <input
              type="text"
              placeholder="Search by room name or participant..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="flex-1 px-4 py-2.5 text-sm border border-gray-200 rounded-xl shadow-sm focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 focus:outline-none hover:border-gray-300 transition-colors"
            />
            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setCurrentPage(0);
              }}
              className={`${selectClass} min-w-[140px]`}
              style={{
                backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`
              }}
            >
              {ROOM_STATUS_FILTER_OPTIONS.map((opt) => (
                <option key={opt.value || 'all'} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <select
              value={timePreset}
              onChange={(e) => {
                setTimePreset(e.target.value);
                setCurrentPage(0);
              }}
              className={`${selectClass} min-w-[160px]`}
              style={{
                backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`
              }}
            >
              {TIME_RANGE_PRESET_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>
          <button
            onClick={fetchRooms}
            className="px-4 py-2.5 bg-blue-600 text-white text-sm font-medium rounded-xl shadow-sm hover:bg-blue-700 hover:shadow transition inline-flex items-center"
          >
            <RefreshIcon />
            Refresh
          </button>
        </div>
      </div>

      <div className="bg-white shadow-md rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                  Room Name
                </th>
                <th className="px-6 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-6 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                  Created At
                </th>
                <th className="px-6 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                  Completed At
                </th>
                <th className="px-6 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            {loading ? (
              <TableSkeleton />
            ) : (
            <tbody className="bg-white divide-y divide-gray-200">
              {rooms.length === 0 ? (
                <tr>
                  <td colSpan="5" className="px-6 py-4 text-center text-gray-500">
                    No rooms found
                  </td>
                </tr>
              ) : (
                rooms.map((room) => (
                  <tr
                    key={room._id}
                    className="hover:bg-gray-50 cursor-pointer transition"
                    onClick={() => goToRoom(room._id)}
                  >
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="text-sm font-medium text-gray-900">
                        {room.room_name}
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      {getStatusBadge(room.status)}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {formatDate(room.created_at)}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {formatDate(room.completed_at)}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          goToRoom(room._id);
                        }}
                        className="text-blue-600 hover:text-blue-900 font-medium"
                      >
                        View Details
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
            )}
          </table>
        </div>

        {/* Pagination */}
        {!loading && totalPages > 1 && (
          <div className="bg-white px-4 py-3 flex items-center justify-between border-t border-gray-200 sm:px-6">
            <div className="flex-1 flex justify-between sm:hidden">
              <button
                onClick={() => setCurrentPage(Math.max(0, currentPage - 1))}
                disabled={currentPage === 0}
                className="relative inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
              >
                Previous
              </button>
              <button
                onClick={() => setCurrentPage(Math.min(totalPages - 1, currentPage + 1))}
                disabled={currentPage >= totalPages - 1}
                className="ml-3 relative inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
              >
                Next
              </button>
            </div>
            <div className="hidden sm:flex-1 sm:flex sm:items-center sm:justify-between">
              <div>
                <p className="text-sm text-gray-700">
                  Showing <span className="font-medium">{currentPage * ITEMS_PER_PAGE + 1}</span> to{' '}
                  <span className="font-medium">
                    {Math.min((currentPage + 1) * ITEMS_PER_PAGE, totalRooms)}
                  </span>{' '}
                  of <span className="font-medium">{totalRooms}</span> results
                </p>
              </div>
              <div>
                <nav className="relative z-0 inline-flex rounded-md shadow-sm -space-x-px">
                  <button
                    onClick={() => setCurrentPage(Math.max(0, currentPage - 1))}
                    disabled={currentPage === 0}
                    className="relative inline-flex items-center px-2 py-2 rounded-l-md border border-gray-300 bg-white text-sm font-medium text-gray-500 hover:bg-gray-50 disabled:opacity-50"
                  >
                    Previous
                  </button>
                  <span className="relative inline-flex items-center px-4 py-2 border border-gray-300 bg-white text-sm font-medium text-gray-700">
                    Page {currentPage + 1} of {totalPages}
                  </span>
                  <button
                    onClick={() => setCurrentPage(Math.min(totalPages - 1, currentPage + 1))}
                    disabled={currentPage >= totalPages - 1}
                    className="relative inline-flex items-center px-2 py-2 rounded-r-md border border-gray-300 bg-white text-sm font-medium text-gray-500 hover:bg-gray-50 disabled:opacity-50"
                  >
                    Next
                  </button>
                </nav>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default RoomList;
