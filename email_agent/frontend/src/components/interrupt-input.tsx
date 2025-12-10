'use client'

import { useState } from 'react'
import { HumanInterrupt, HumanResponse } from '@/lib/types'
import { Check, X, Edit, MessageSquare, Loader2 } from 'lucide-react'

interface InterruptInputProps {
  interrupt: HumanInterrupt
  onSubmit: (response: HumanResponse) => void
  isLoading: boolean
}

export function InterruptInput({ interrupt, onSubmit, isLoading }: InterruptInputProps) {
  const [mode, setMode] = useState<'buttons' | 'edit' | 'respond'>('buttons')
  const [editedArgs, setEditedArgs] = useState(
    JSON.stringify(interrupt.action_request.args, null, 2)
  )
  const [responseText, setResponseText] = useState('')
  const [editError, setEditError] = useState<string | null>(null)

  const { config, action_request, description } = interrupt

  const handleAccept = () => {
    onSubmit({ type: 'accept', args: null })
  }

  const handleIgnore = () => {
    onSubmit({ type: 'ignore', args: null })
  }

  const handleEdit = () => {
    setEditError(null)
    try {
      const parsed = JSON.parse(editedArgs)
      onSubmit({
        type: 'edit',
        args: {
          action: action_request.action,
          args: parsed
        }
      })
    } catch (e) {
      setEditError('Invalid JSON')
    }
  }

  const handleRespond = () => {
    if (responseText.trim()) {
      onSubmit({ type: 'response', args: responseText.trim() })
    }
  }

  return (
    <div className="border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
      {/* Action Info */}
      <div className="p-4 bg-amber-50 dark:bg-amber-900/20 border-b border-amber-200 dark:border-amber-800">
        <div className="flex items-start gap-3">
          <div className="w-8 h-8 rounded-full bg-amber-100 dark:bg-amber-800 flex items-center justify-center flex-shrink-0">
            <span className="text-lg">🔔</span>
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="font-medium text-amber-900 dark:text-amber-100">
              Action Requires Approval
            </h3>
            <p className="text-sm text-amber-700 dark:text-amber-300 mt-1">
              {description || `Tool: ${action_request.action}`}
            </p>
            <div className="mt-2 p-2 bg-white dark:bg-gray-800 rounded border border-amber-200 dark:border-amber-700">
              <code className="text-xs text-gray-700 dark:text-gray-300 whitespace-pre-wrap">
                {JSON.stringify(action_request.args, null, 2)}
              </code>
            </div>
          </div>
        </div>
      </div>

      {/* Action Buttons / Edit / Respond */}
      <div className="p-4">
        {mode === 'buttons' && (
          <div className="flex flex-wrap gap-2">
            {config.allow_accept && (
              <button
                onClick={handleAccept}
                disabled={isLoading}
                className="flex items-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white rounded-lg transition-colors"
              >
                {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                Accept
              </button>
            )}
            
            {config.allow_edit && (
              <button
                onClick={() => setMode('edit')}
                disabled={isLoading}
                className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg transition-colors"
              >
                <Edit className="w-4 h-4" />
                Edit
              </button>
            )}
            
            {config.allow_respond && (
              <button
                onClick={() => setMode('respond')}
                disabled={isLoading}
                className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg transition-colors"
              >
                <MessageSquare className="w-4 h-4" />
                Respond
              </button>
            )}
            
            {config.allow_ignore && (
              <button
                onClick={handleIgnore}
                disabled={isLoading}
                className="flex items-center gap-2 px-4 py-2 bg-gray-500 hover:bg-gray-600 disabled:opacity-50 text-white rounded-lg transition-colors"
              >
                <X className="w-4 h-4" />
                Ignore
              </button>
            )}
          </div>
        )}

        {mode === 'edit' && (
          <div className="space-y-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Edit Arguments (JSON)
              </label>
              <textarea
                value={editedArgs}
                onChange={(e) => {
                  setEditedArgs(e.target.value)
                  setEditError(null)
                }}
                className="w-full h-32 p-3 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white font-mono text-sm resize-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                disabled={isLoading}
              />
              {editError && (
                <p className="text-sm text-red-500 mt-1">{editError}</p>
              )}
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleEdit}
                disabled={isLoading}
                className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg transition-colors"
              >
                {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                Submit Changes
              </button>
              <button
                onClick={() => setMode('buttons')}
                disabled={isLoading}
                className="px-4 py-2 bg-gray-200 hover:bg-gray-300 dark:bg-gray-600 dark:hover:bg-gray-500 text-gray-700 dark:text-white rounded-lg transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {mode === 'respond' && (
          <div className="space-y-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Your Response
              </label>
              <textarea
                value={responseText}
                onChange={(e) => setResponseText(e.target.value)}
                placeholder="Type your response here..."
                className="w-full h-24 p-3 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm resize-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                disabled={isLoading}
              />
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleRespond}
                disabled={isLoading || !responseText.trim()}
                className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg transition-colors"
              >
                {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <MessageSquare className="w-4 h-4" />}
                Send Response
              </button>
              <button
                onClick={() => setMode('buttons')}
                disabled={isLoading}
                className="px-4 py-2 bg-gray-200 hover:bg-gray-300 dark:bg-gray-600 dark:hover:bg-gray-500 text-gray-700 dark:text-white rounded-lg transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
