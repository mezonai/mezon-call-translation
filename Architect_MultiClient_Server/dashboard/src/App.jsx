import { useState } from 'react'
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import RoomList from './components/RoomList'
import RoomDetail from './components/RoomDetail'
import Login from './components/Login'
import Callback from './components/Callback'
import LoadingSpinner from './components/LoadingSpinner'

// Protected Route wrapper component
const ProtectedRoute = ({ children }) => {
  const { isAuthenticated, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <LoadingSpinner size="large" />
      </div>
    );
  }

  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }

  return children;
};

// Main App Content with Navigation
const AppContent = () => {
  const { user, logout, isAuthenticated } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <nav className="bg-white shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between h-16 items-center">
            <div className="flex items-center">
              <h1 className="text-2xl font-bold text-gray-900">
                AI Agent Dashboard
              </h1>
            </div>

            {isAuthenticated() && user && (
              <div className="flex items-center space-x-4">
                <div className="flex items-center space-x-3">
                  {user.avatar && (
                    <img
                      src={user.avatar}
                      alt={user.display_name || user.username}
                      className="h-8 w-8 rounded-full object-cover"
                    />
                  )}
                  <div className="text-sm">
                    <p className="font-medium text-gray-900">
                      {user.display_name}
                    </p>
                    <p className="text-gray-500 text-xs">
                      {user.username}
                    </p>
                  </div>
                </div>

                <button
                  onClick={handleLogout}
                  className="inline-flex items-center px-3 py-2 border border-transparent text-sm leading-4 font-medium rounded-md text-white bg-red-600 hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500 transition-colors"
                >
                  Logout
                </button>
              </div>
            )}
          </div>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/callback" element={<Callback />} />
          <Route
            path="/"
            element={
              <ProtectedRoute>
                <RoomList />
              </ProtectedRoute>
            }
          />
          <Route
            path="/room/:roomId"
            element={
              <ProtectedRoute>
                <RoomDetail />
              </ProtectedRoute>
            }
          />
        </Routes>
      </main>
    </div>
  );
};

function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  )
}

export default App
