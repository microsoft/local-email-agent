'use client'

import { useState, useEffect, useCallback } from 'react'
import { InboxList } from '@/components/inbox-list'
import { ThreadView } from '@/components/thread-view'
import { NewRunDialog } from '@/components/new-run-dialog'
import { Thread, ThreadStatus } from '@/lib/types'
import { fetchThreads } from '@/lib/api'
import { streamingManager, ThreadStreamState } from '@/lib/streaming-manager'
import { Inbox, Plus, RefreshCw } from 'lucide-react'

export default function Home() {
  const [threads, setThreads] = useState<Thread[]>([])
  const [selectedThread, setSelectedThread] = useState<Thread | null>(null)
  const [statusFilter, setStatusFilter] = useState<ThreadStatus | 'all'>('all')
  const [isLoading, setIsLoading] = useState(true)
  const [showNewRun, setShowNewRun] = useState(false)
  
  // Track streaming threads - updated by streaming manager callbacks
  const [streamingThreadIds, setStreamingThreadIds] = useState<Set<string>>(new Set())
  
  // Stream states by thread ID - for passing to ThreadView
  const [streamStates, setStreamStates] = useState<Map<string, ThreadStreamState>>(new Map())

  // Set up streaming manager callbacks
  useEffect(() => {
    streamingManager.setCallbacks({
      onUpdate: (threadId, state) => {
        setStreamStates(prev => {
          const next = new Map(prev)
          next.set(threadId, state)
          return next
        })
        setStreamingThreadIds(streamingManager.getAllStreamingThreadIds())
      },
      onComplete: (threadId) => {
        setStreamingThreadIds(streamingManager.getAllStreamingThreadIds())
        loadThreads() // Refresh thread list
      }
    })
  }, [])

  const loadThreads = async () => {
    setIsLoading(true)
    try {
      const data = await fetchThreads(statusFilter === 'all' ? undefined : statusFilter)
      
      // Merge fetched threads with any active streaming threads that may not be in the server yet
      setThreads(prev => {
        // Get IDs of threads currently streaming
        const currentStreamingIds = streamingManager.getAllStreamingThreadIds()
        
        // Keep any streaming threads that aren't in the fetched data
        // (they might not be persisted to the server yet)
        const streamingNotInFetch = prev.filter(t => 
          currentStreamingIds.has(t.thread_id) && 
          !data.find(d => d.thread_id === t.thread_id)
        )
        
        // Merge: streaming threads first, then fetched data
        const merged = [...streamingNotInFetch, ...data]
        
        // Dedupe by thread_id (keep first occurrence)
        const seen = new Set<string>()
        return merged.filter(t => {
          if (seen.has(t.thread_id)) return false
          seen.add(t.thread_id)
          return true
        })
      })
    } catch (error) {
      console.error('Failed to load threads:', error)
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    loadThreads()
    // Poll for updates every 5 seconds
    const interval = setInterval(loadThreads, 5000)
    return () => clearInterval(interval)
  }, [statusFilter])

  const handleThreadSelect = (thread: Thread) => {
    setSelectedThread(thread)
  }

  const handleThreadDelete = (threadId: string) => {
    // Stop any streaming for this thread
    streamingManager.clearStream(threadId)
    
    // Remove from local state
    setThreads(prev => prev.filter(t => t.thread_id !== threadId))
    setStreamStates(prev => {
      const next = new Map(prev)
      next.delete(threadId)
      return next
    })
    
    // If this was the selected thread, deselect it
    if (selectedThread?.thread_id === threadId) {
      setSelectedThread(null)
    }
  }

  const handleThreadUpdate = () => {
    loadThreads()
    if (selectedThread) {
      // Refresh the selected thread
      const updated = threads.find(t => t.thread_id === selectedThread.thread_id)
      if (updated) {
        setSelectedThread(updated)
      }
    }
  }

  const handleStartStreaming = (question: string, threadId: string) => {
    // Create a temporary thread for immediate display
    const newThread: Thread = {
      thread_id: threadId,
      status: 'busy',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      question: question
    }
    
    // Add to threads list (avoid duplicates) and select it
    setThreads(prev => {
      // Check if thread already exists
      if (prev.find(t => t.thread_id === threadId)) {
        return prev
      }
      return [newThread, ...prev]
    })
    setSelectedThread(newThread)
    
    // Start streaming via manager (persists even when switching threads)
    streamingManager.startStream(threadId, question)
    setStreamingThreadIds(streamingManager.getAllStreamingThreadIds())
  }

  const handleFollowUp = (question: string) => {
    if (!selectedThread) return
    
    // Start streaming via manager
    streamingManager.startStream(selectedThread.thread_id, question)
    setStreamingThreadIds(streamingManager.getAllStreamingThreadIds())
  }

  const handleResume = (response: { type: string; args?: any }) => {
    if (!selectedThread) return
    
    // Resume with streaming via manager
    streamingManager.resumeStream(selectedThread.thread_id, response as any)
    setStreamingThreadIds(streamingManager.getAllStreamingThreadIds())
  }

  // Get streaming state for the selected thread
  const selectedStreamState = selectedThread 
    ? streamStates.get(selectedThread.thread_id)
    : undefined

  return (
    <div className="flex h-screen bg-gray-50 dark:bg-gray-900">
      {/* Sidebar */}
      <div className="w-80 border-r border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 flex flex-col">
        {/* Header */}
        <div className="p-4 border-b border-gray-200 dark:border-gray-700">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Inbox className="w-6 h-6 text-blue-600" />
              <h1 className="text-xl font-bold text-gray-900 dark:text-white">Agent Inbox</h1>
            </div>
            <button
              onClick={loadThreads}
              className="p-2 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
              title="Refresh"
            >
              <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
            </button>
          </div>
          
          {/* New Run Button - Always enabled */}
          <button
            onClick={() => setShowNewRun(true)}
            className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors"
          >
            <Plus className="w-4 h-4" />
            New Run
            {streamingThreadIds.size > 0 && (
              <span className="ml-1 px-1.5 py-0.5 text-xs bg-blue-500 rounded-full">
                {streamingThreadIds.size} running
              </span>
            )}
          </button>
        </div>

        {/* Filter Tabs */}
        <div className="flex border-b border-gray-200 dark:border-gray-700">
          {(['all', 'interrupted', 'idle', 'busy', 'error'] as const).map((status) => (
            <button
              key={status}
              onClick={() => setStatusFilter(status)}
              className={`flex-1 px-3 py-2 text-sm font-medium capitalize transition-colors ${
                statusFilter === status
                  ? 'border-b-2 border-blue-600 text-blue-600'
                  : 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
              }`}
            >
              {status}
            </button>
          ))}
        </div>

        {/* Thread List */}
        <InboxList
          threads={threads}
          selectedThread={selectedThread}
          onSelect={handleThreadSelect}
          onDelete={handleThreadDelete}
          isLoading={isLoading}
          streamingThreadIds={streamingThreadIds}
        />
      </div>

      {/* Main Content */}
      <div className="flex-1 flex flex-col">
        {selectedThread ? (
          <ThreadView
            key={selectedThread.thread_id}
            thread={selectedThread}
            onUpdate={handleThreadUpdate}
            streamState={selectedStreamState}
            onFollowUpSent={handleFollowUp}
            onResume={handleResume}
          />
        ) : (
          <div className="flex-1 flex items-center justify-center text-gray-500">
            <div className="text-center">
              <Inbox className="w-16 h-16 mx-auto mb-4 opacity-50" />
              <p className="text-lg">Select a thread to view details</p>
              <p className="text-sm mt-2">or create a new run to get started</p>
            </div>
          </div>
        )}
      </div>

      {/* New Run Dialog */}
      {showNewRun && (
        <NewRunDialog
          onClose={() => setShowNewRun(false)}
          onStartStreaming={handleStartStreaming}
        />
      )}
    </div>
  )
}
