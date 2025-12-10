'use client'

import { useState } from 'react'
import { Thread } from '@/lib/types'
import { deleteThread } from '@/lib/api'
import { AlertCircle, CheckCircle, Clock, Loader2, MessageSquare, MoreVertical, Trash2 } from 'lucide-react'

interface InboxListProps {
  threads: Thread[]
  selectedThread: Thread | null
  onSelect: (thread: Thread) => void
  onDelete: (threadId: string) => void
  isLoading: boolean
  streamingThreadIds?: Set<string>  // Track which threads are currently streaming
}

const StatusIcon = ({ status, isStreaming }: { status: Thread['status']; isStreaming?: boolean }) => {
  // If streaming, always show spinning indicator
  if (isStreaming) {
    return <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />
  }
  
  switch (status) {
    case 'interrupted':
      return <AlertCircle className="w-4 h-4 text-amber-500" />
    case 'idle':
      return <CheckCircle className="w-4 h-4 text-green-500" />
    case 'busy':
      return <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />
    case 'error':
      return <AlertCircle className="w-4 h-4 text-red-500" />
    default:
      return <Clock className="w-4 h-4 text-gray-400" />
  }
}

const formatTime = (isoString: string) => {
  const date = new Date(isoString)
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  
  if (diff < 60000) return 'Just now'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
  return date.toLocaleDateString()
}

export function InboxList({ threads, selectedThread, onSelect, onDelete, isLoading, streamingThreadIds }: InboxListProps) {
  const [hoveredThread, setHoveredThread] = useState<string | null>(null)
  const [menuOpenThread, setMenuOpenThread] = useState<string | null>(null)
  const [deletingThread, setDeletingThread] = useState<string | null>(null)

  const handleDelete = async (e: React.MouseEvent, threadId: string) => {
    e.stopPropagation()
    setDeletingThread(threadId)
    
    try {
      await deleteThread(threadId)
      onDelete(threadId)
      setMenuOpenThread(null)
    } catch (error) {
      console.error('Failed to delete thread:', error)
    } finally {
      setDeletingThread(null)
    }
  }

  const handleMenuClick = (e: React.MouseEvent, threadId: string) => {
    e.stopPropagation()
    setMenuOpenThread(menuOpenThread === threadId ? null : threadId)
  }

  if (isLoading && threads.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
      </div>
    )
  }

  if (threads.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center p-4 text-gray-500">
        <div className="text-center">
          <MessageSquare className="w-8 h-8 mx-auto mb-2 opacity-50" />
          <p>No threads found</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto">
      {threads.map((thread) => (
        <div
          key={thread.thread_id}
          className="relative"
          onMouseEnter={() => setHoveredThread(thread.thread_id)}
          onMouseLeave={() => {
            setHoveredThread(null)
            if (menuOpenThread === thread.thread_id) {
              setMenuOpenThread(null)
            }
          }}
        >
          <button
            onClick={() => onSelect(thread)}
            className={`w-full p-4 text-left border-b border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors ${
              selectedThread?.thread_id === thread.thread_id
                ? 'bg-blue-50 dark:bg-blue-900/20 border-l-4 border-l-blue-600'
                : ''
            }`}
          >
            <div className="flex items-start gap-3">
              <StatusIcon status={thread.status} isStreaming={streamingThreadIds?.has(thread.thread_id)} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-medium text-sm text-gray-900 dark:text-white truncate pr-6">
                    {thread.thread_id.slice(0, 20)}...
                  </span>
                  <span className="text-xs text-gray-500 ml-2 flex-shrink-0">
                    {formatTime(thread.updated_at)}
                  </span>
                </div>
                {thread.question && (
                  <p className="text-sm text-gray-600 dark:text-gray-300 line-clamp-2">
                    {thread.question}
                  </p>
                )}
                {thread.status === 'interrupted' && thread.interrupt_description && (
                  <p className="text-xs text-amber-600 dark:text-amber-400 mt-1 line-clamp-1">
                    ⏸️ {thread.interrupt_description}
                  </p>
                )}
              </div>
            </div>
          </button>
          
          {/* Three-dot menu button - shows on hover */}
          {(hoveredThread === thread.thread_id || menuOpenThread === thread.thread_id) && (
            <button
              onClick={(e) => handleMenuClick(e, thread.thread_id)}
              className="absolute right-2 top-4 p-1.5 rounded-md hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors z-10"
            >
              <MoreVertical className="w-4 h-4 text-gray-500" />
            </button>
          )}
          
          {/* Dropdown menu */}
          {menuOpenThread === thread.thread_id && (
            <div className="absolute right-2 top-10 bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 py-1 z-20 min-w-[120px]">
              <button
                onClick={(e) => handleDelete(e, thread.thread_id)}
                disabled={deletingThread === thread.thread_id}
                className="w-full px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 flex items-center gap-2 disabled:opacity-50"
              >
                {deletingThread === thread.thread_id ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Trash2 className="w-4 h-4" />
                )}
                Delete
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
