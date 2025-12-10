'use client'

import { useState, useEffect, useRef } from 'react'
import { Thread, ThreadDetail, HumanInterrupt, Message } from '@/lib/types'
import { fetchThread, resumeThread } from '@/lib/api'
import { ThreadStreamState, StreamingStep } from '@/lib/streaming-manager'
import { InterruptInput } from './interrupt-input'
import { 
  AlertCircle, 
  CheckCircle, 
  Loader2, 
  MessageSquare,
  User,
  Bot,
  Wrench,
  ChevronDown,
  ChevronRight,
  Play,
  Zap,
  Activity,
  Send
} from 'lucide-react'

interface StreamingState {
  isStreaming: boolean
  steps: StreamingStep[]
  finalAnswer?: string
  error?: string
}

// Simple activity log panel - clean and readable
const ActivityPanel = ({ streaming, isCollapsed, onToggleCollapse }: {
  streaming: StreamingState
  isCollapsed: boolean
  onToggleCollapse: () => void
}) => {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [expandedResults, setExpandedResults] = useState<Set<number>>(new Set())
  
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [streaming.steps])

  const toggleExpand = (id: number) => {
    setExpandedResults(prev => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  return (
    <div className="border-l border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 flex flex-col" 
         style={{ width: isCollapsed ? '48px' : '380px', minWidth: isCollapsed ? '48px' : '380px' }}>
      {/* Panel Header */}
      <div className="p-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
        {!isCollapsed && (
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-gray-500" />
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Activity</span>
            {streaming.isStreaming && (
              <Loader2 className="w-3 h-3 animate-spin text-blue-500" />
            )}
          </div>
        )}
        <button 
          onClick={onToggleCollapse}
          className="p-1 hover:bg-gray-200 dark:hover:bg-gray-700 rounded"
        >
          {isCollapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </button>
      </div>
      
      {/* Activity Log */}
      {!isCollapsed && (
        <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-2">
          {streaming.steps.length === 0 && !streaming.isStreaming && (
            <p className="text-xs text-gray-400 text-center py-4">No activity yet</p>
          )}
          
          {streaming.steps.map((step) => (
            <div key={step.id} className="text-xs">
              {step.type === 'thinking' || step.type === 'status' ? (
                // Simple status message - no dropdown
                <div className="flex items-center gap-2 text-gray-500 dark:text-gray-400 py-1">
                  <span className="text-blue-500">●</span>
                  <span>{step.message}</span>
                </div>
              ) : step.type === 'tool_call' ? (
                // Tool call with expandable details
                <div className="bg-white dark:bg-gray-800 rounded border border-blue-200 dark:border-blue-700 overflow-hidden">
                  <div className="flex items-center gap-2 px-2 py-1.5 bg-blue-50 dark:bg-blue-900/20">
                    <Wrench className="w-3 h-3 text-blue-500" />
                    <span className="font-medium text-gray-700 dark:text-gray-300">{step.tool}</span>
                    <span className="text-blue-500 ml-auto">→</span>
                  </div>
                  {step.args && Object.keys(step.args).length > 0 && (
                    <div className="px-2 py-1 text-gray-600 dark:text-gray-400 border-t border-gray-100 dark:border-gray-700">
                      {Object.entries(step.args).map(([key, value]) => (
                        <div key={key} className="truncate">
                          <span className="text-gray-400">{key}:</span> {typeof value === 'string' ? value.slice(0, 50) : JSON.stringify(value).slice(0, 50)}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ) : step.type === 'tool_result' ? (
                // Tool result - expandable to show full content
                <div className="bg-white dark:bg-gray-800 rounded border border-green-200 dark:border-green-800 overflow-hidden">
                  <button
                    onClick={() => toggleExpand(step.id)}
                    className="w-full flex items-center gap-2 px-2 py-1.5 bg-green-50 dark:bg-green-900/20 hover:bg-green-100 dark:hover:bg-green-900/30"
                  >
                    {expandedResults.has(step.id) ? (
                      <ChevronDown className="w-3 h-3 text-green-500" />
                    ) : (
                      <ChevronRight className="w-3 h-3 text-green-500" />
                    )}
                    <CheckCircle className="w-3 h-3 text-green-500" />
                    <span className="font-medium text-gray-700 dark:text-gray-300">{step.tool}</span>
                    <span className="text-green-500 ml-auto">✓</span>
                  </button>
                  {step.result && (
                    <div className={`px-2 py-1 text-gray-600 dark:text-gray-400 border-t border-gray-100 dark:border-gray-700 overflow-y-auto ${
                      expandedResults.has(step.id) ? 'max-h-96' : 'max-h-16'
                    }`}>
                      <pre className="whitespace-pre-wrap text-xs font-mono">
                        {expandedResults.has(step.id) 
                          ? step.result 
                          : (step.result.slice(0, 100) + (step.result.length > 100 ? '...' : ''))
                        }
                      </pre>
                      {!expandedResults.has(step.id) && step.result.length > 100 && (
                        <span className="text-green-600 dark:text-green-400 text-xs">Click to expand</span>
                      )}
                    </div>
                  )}
                </div>
              ) : null}
            </div>
          ))}
          
          {streaming.isStreaming && (
            <div className="flex items-center gap-2 text-gray-500 py-1">
              <Loader2 className="w-3 h-3 animate-spin" />
              <span className="text-xs">Processing...</span>
            </div>
          )}
        </div>
      )}
      
      {/* Collapsed state shows icon */}
      {isCollapsed && (
        <div className="flex-1 flex flex-col items-center pt-4">
          <Activity className="w-5 h-5 text-gray-400" />
          {streaming.isStreaming && (
            <Loader2 className="w-4 h-4 animate-spin text-blue-500 mt-2" />
          )}
          {streaming.steps.length > 0 && (
            <span className="text-xs text-gray-500 mt-1">{streaming.steps.length}</span>
          )}
        </div>
      )}
      
      {/* Error display */}
      {!isCollapsed && streaming.error && (
        <div className="p-3 border-t border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20">
          <p className="text-xs text-red-600 dark:text-red-400">{streaming.error}</p>
        </div>
      )}
    </div>
  )
}

interface ThreadViewProps {
  thread: Thread
  onUpdate: () => void
  streamState?: ThreadStreamState  // Stream state from manager (persists across tab switches)
  onFollowUpSent?: (question: string) => void
  onResume?: (response: { type: string; args?: any }) => void  // Streaming resume handler
}

// Helper to check if a message should be shown in chat
// Only show: Human messages and AI messages with actual content (not tool calls)
const shouldShowInChat = (message: Message): boolean => {
  // Always hide tool messages
  if (message.type === 'ToolMessage') return false
  
  // Hide AI messages that only have tool calls (no real content)
  if (message.type === 'AIMessage') {
    // If it has tool_calls and no meaningful content, hide it
    if (message.tool_calls && message.tool_calls.length > 0) {
      // Check if content is empty or just whitespace
      if (!message.content || message.content.trim() === '') {
        return false
      }
      // Check if content is just "Calling: ..." which we generate
      if (message.content.startsWith('Calling:')) {
        return false
      }
    }
    // Show AI messages with actual content
    return !!message.content && message.content.trim() !== ''
  }
  
  // Show human messages
  return true
}

// Extract the final answer from messages (from Done tool call)
const extractFinalAnswer = (messages: Message[]): string | null => {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]
    if (msg.type === 'AIMessage' && msg.tool_calls) {
      for (const tc of msg.tool_calls) {
        if (tc.name === 'Done' && tc.args?.answer) {
          return tc.args.answer
        }
      }
    }
  }
  return null
}

// Extract ALL Done answers from messages in order (for multi-turn conversations)
const extractAllDoneAnswers = (messages: Message[]): { index: number; answer: string }[] => {
  const answers: { index: number; answer: string }[] = []
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i]
    if (msg.type === 'AIMessage' && msg.tool_calls) {
      for (const tc of msg.tool_calls) {
        if (tc.name === 'Done' && tc.args?.answer) {
          answers.push({ index: i, answer: tc.args.answer })
        }
      }
    }
  }
  return answers
}

// Build chat messages including Done answers interspersed at correct positions
const buildChatMessages = (messages: Message[]): Message[] => {
  const result: Message[] = []
  const doneAnswers = extractAllDoneAnswers(messages)
  let doneAnswerIdx = 0
  
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i]
    
    // Add the message if it should be shown in chat
    if (shouldShowInChat(msg)) {
      result.push(msg)
    }
    
    // Check if there's a Done answer at this position
    while (doneAnswerIdx < doneAnswers.length && doneAnswers[doneAnswerIdx].index === i) {
      const answer = doneAnswers[doneAnswerIdx].answer
      // Check if this answer isn't already in result
      const alreadyShown = result.some(m => 
        m.type === 'AIMessage' && m.content === answer
      )
      if (!alreadyShown) {
        result.push({ type: 'AIMessage', content: answer })
      }
      doneAnswerIdx++
    }
  }
  
  return result
}

const MessageBubble = ({ message }: { message: Message }) => {
  const isHuman = message.type === 'HumanMessage'
  
  return (
    <div className={`flex gap-3 ${isHuman ? 'justify-end' : 'justify-start'}`}>
      {!isHuman && (
        <div className="w-8 h-8 rounded-full flex items-center justify-center bg-blue-100 dark:bg-blue-900">
          <Bot className="w-4 h-4 text-blue-600 dark:text-blue-400" />
        </div>
      )}
      
      <div className={`max-w-[70%] rounded-lg px-4 py-2 ${
        isHuman 
          ? 'bg-blue-600 text-white'
          : 'bg-gray-100 dark:bg-gray-700'
      }`}>
        <p className={`text-sm whitespace-pre-wrap ${
          isHuman ? 'text-white' : 'text-gray-900 dark:text-gray-100'
        }`}>
          {message.content}
        </p>
      </div>
      
      {isHuman && (
        <div className="w-8 h-8 rounded-full bg-gray-200 dark:bg-gray-600 flex items-center justify-center">
          <User className="w-4 h-4 text-gray-600 dark:text-gray-300" />
        </div>
      )}
    </div>
  )
}

export function ThreadView({ 
  thread, 
  onUpdate, 
  streamState,
  onFollowUpSent,
  onResume
}: ThreadViewProps) {
  const [detail, setDetail] = useState<ThreadDetail | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isResuming, setIsResuming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  
  // Follow-up input state
  const [followUpQuestion, setFollowUpQuestion] = useState('')
  
  // Pending message that hasn't been saved to thread yet (shows immediately on submit)
  const [pendingMessage, setPendingMessage] = useState<string | null>(null)
  
  // Activity panel state
  const [activityCollapsed, setActivityCollapsed] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Convert stream state to local streaming state format
  const streaming: StreamingState = streamState ? {
    isStreaming: streamState.isStreaming,
    steps: streamState.steps,
    finalAnswer: streamState.finalAnswer,
    error: streamState.error
  } : {
    isStreaming: false,
    steps: []
  }

  const loadDetail = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const data = await fetchThread(thread.thread_id)
      setDetail(data)
      // Clear pending message once messages are loaded (it should be in the thread now)
      if (data.messages && data.messages.length > 0) {
        setPendingMessage(null)
      }
    } catch (err: any) {
      setError(err.message)
    } finally {
      setIsLoading(false)
    }
  }

  // Load detail when thread changes or when streaming completes
  useEffect(() => {
    loadDetail()
  }, [thread.thread_id])
  
  // Reload detail when streaming completes OR when interrupted
  useEffect(() => {
    if (streamState && !streamState.isStreaming) {
      // Reload whether it's done, errored, or interrupted
      loadDetail()
      onUpdate()
    }
  }, [streamState?.isStreaming])

  // Auto-scroll to bottom when streaming
  useEffect(() => {
    if (streaming.isStreaming || streaming.finalAnswer) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [streaming.steps, streaming.finalAnswer])

  const handleResume = async (response: { type: string; args?: any }) => {
    setIsResuming(true)
    setError(null)
    
    // Use streaming resume if handler provided, otherwise fall back to non-streaming
    if (onResume) {
      onResume(response)
      setIsResuming(false)
    } else {
      try {
        await resumeThread(thread.thread_id, response as any)
        onUpdate()
        loadDetail()
      } catch (err: any) {
        setError(err.message)
      } finally {
        setIsResuming(false)
      }
    }
  }

  const handleFollowUpSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!followUpQuestion.trim() || streaming.isStreaming) return
    
    const question = followUpQuestion.trim()
    setFollowUpQuestion('')
    
    // Show the question immediately as a pending message
    setPendingMessage(question)
    
    // Notify parent to start streaming (manager handles the actual stream)
    onFollowUpSent?.(question)
  }

  // Can send follow-up if:
  // - Thread status is idle (not busy/interrupted/error) and not currently streaming, OR
  // - Streaming just completed (finalAnswer exists)
  // - Also show input during streaming (disabled) so it's visible
  const canSendFollowUp = (
    (detail?.status === 'idle') || 
    (streaming.finalAnswer && !streaming.isStreaming) ||
    (detail?.messages && detail.messages.length > 0 && detail?.status !== 'interrupted' && detail?.status !== 'error')
  ) && !streaming.isStreaming

  if (isLoading && !streaming.isStreaming) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
      </div>
    )
  }

  if (error && !streaming.isStreaming) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center text-red-500">
          <AlertCircle className="w-8 h-8 mx-auto mb-2" />
          <p>{error}</p>
        </div>
      </div>
    )
  }

  // Show activity panel if streaming, has steps, or interrupted with steps
  const showStreaming = streaming.isStreaming || streaming.steps.length > 0 || streamState?.interrupt
  
  // Show the question from stream state if we're loading detail or interrupted
  const displayQuestion = streamState?.question || thread.question

  return (
    <div className="flex-1 flex">
      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col">
        {/* Header */}
        <div className="p-4 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="font-semibold text-gray-900 dark:text-white">
                Thread: {thread.thread_id.slice(0, 20)}...
              </h2>
              <div className="flex items-center gap-2 mt-1">
                <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
                  streaming.isStreaming ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400' :
                  detail?.status === 'interrupted' ? 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400' :
                  detail?.status === 'idle' ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400' :
                  detail?.status === 'busy' ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400' :
                  'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400'
                }`}>
                  {streaming.isStreaming && <Loader2 className="w-3 h-3 animate-spin" />}
                  {!streaming.isStreaming && detail?.status === 'interrupted' && <AlertCircle className="w-3 h-3" />}
                  {!streaming.isStreaming && detail?.status === 'idle' && <CheckCircle className="w-3 h-3" />}
                  {!streaming.isStreaming && detail?.status === 'busy' && <Loader2 className="w-3 h-3 animate-spin" />}
                  {streaming.isStreaming ? 'streaming' : detail?.status}
                </span>
                {detail && (
                  <span className="text-xs text-gray-500">
                    Updated {new Date(detail.updated_at).toLocaleString()}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Messages - Clean chat area (only user questions and AI final answers) */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4 bg-gray-50 dark:bg-gray-900">
          {detail?.messages && detail.messages.length > 0 ? (
            <>
              {/* Build and render chat messages including all Done answers in order */}
              {buildChatMessages(detail.messages).map((msg, i) => (
                <MessageBubble key={i} message={msg} />
              ))}
              
              {/* Show pending follow-up message immediately */}
              {pendingMessage && (
                <MessageBubble 
                  message={{ 
                    type: 'HumanMessage', 
                    content: pendingMessage 
                  }} 
                />
              )}
            </>
          ) : displayQuestion ? (
            // Show the question when streaming, interrupted, or loading
            <MessageBubble 
              message={{ 
                type: 'HumanMessage', 
                content: displayQuestion 
              }} 
            />
          ) : (
            <div className="text-center text-gray-500 py-8">
              <MessageSquare className="w-8 h-8 mx-auto mb-2 opacity-50" />
              <p>No messages yet</p>
            </div>
          )}
          
          {/* Final answer from streaming (before messages are loaded) */}
          {streaming.finalAnswer && !detail?.messages?.length && (
            <MessageBubble 
              message={{ 
                type: 'AIMessage', 
                content: streaming.finalAnswer 
              }} 
            />
          )}
          
          <div ref={messagesEndRef} />
        </div>

        {/* Interrupt Input */}
        {detail?.status === 'interrupted' && detail.interrupt && !streaming.isStreaming && (
          <InterruptInput
            interrupt={detail.interrupt}
            onSubmit={handleResume}
            isLoading={isResuming}
          />
        )}

        {/* Follow-up Input - Show when conversation has content or streaming */}
        {(canSendFollowUp || streaming.isStreaming || streaming.finalAnswer) && detail?.status !== 'interrupted' && (
          <div className="p-4 border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
            <form onSubmit={handleFollowUpSubmit} className="flex gap-2">
              <input
                ref={inputRef}
                type="text"
                value={followUpQuestion}
                onChange={(e) => setFollowUpQuestion(e.target.value)}
                placeholder={streaming.isStreaming ? "Agent is processing..." : "Ask a follow-up question..."}
                disabled={streaming.isStreaming}
                className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg 
                         bg-white dark:bg-gray-700 text-gray-900 dark:text-white
                         placeholder-gray-500 dark:placeholder-gray-400
                         focus:ring-2 focus:ring-blue-500 focus:border-blue-500
                         disabled:opacity-50 disabled:cursor-not-allowed"
              />
              <button
                type="submit"
                disabled={!followUpQuestion.trim() || streaming.isStreaming}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400 
                         disabled:cursor-not-allowed text-white rounded-lg 
                         transition-colors flex items-center gap-2"
              >
                {streaming.isStreaming ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Send className="w-4 h-4" />
                )}
                Send
              </button>
            </form>
          </div>
        )}
      </div>
      
      {/* Activity Side Panel - Separate from chat */}
      {showStreaming && (
        <ActivityPanel 
          streaming={streaming}
          isCollapsed={activityCollapsed}
          onToggleCollapse={() => setActivityCollapsed(!activityCollapsed)}
        />
      )}
    </div>
  )
}
