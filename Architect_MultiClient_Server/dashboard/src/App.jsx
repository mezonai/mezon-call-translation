import { useState } from 'react'
import { Routes, Route } from 'react-router-dom'
import RoomList from './components/RoomList'
import RoomDetail from './components/RoomDetail'

function App() {
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
          </div>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Routes>
          <Route path="/" element={<RoomList />} />
          <Route path="/room/:roomName" element={<RoomDetail />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
