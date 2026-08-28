import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { agentFollowupsApi } from "@/api/endpoints"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { useToast } from "@/components/ui/toast-context"
import { parseUtcTimestamp } from "@/lib/utils"
import { MessageCircleQuestion, Send } from "lucide-react"

function formatTime(iso: string) {
  return parseUtcTimestamp(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

/**
 * Per-trace-entry "ask a follow-up" affordance. Only rendered for agent runs
 * that have a live TrueForge session (run.can_ask_followup) — the follow-up
 * question is answered as a real continuation turn on that same session, so
 * the model genuinely remembers its own prior reasoning rather than being
 * re-fed a fresh restatement of it.
 */
export function AgentRunFollowupPanel({ leadId, agentRunId }: { leadId: string; agentRunId: string }) {
  const [question, setQuestion] = useState("")
  const queryClient = useQueryClient()
  const { push } = useToast()

  const {
    data: followups,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["agent-run-followups", agentRunId],
    queryFn: () => agentFollowupsApi.list(leadId, agentRunId),
  })

  const askFollowup = useMutation({
    mutationFn: (q: string) => agentFollowupsApi.ask(leadId, agentRunId, q),
    onSuccess: () => {
      setQuestion("")
      queryClient.invalidateQueries({ queryKey: ["agent-run-followups", agentRunId] })
    },
    onError: (err: Error) =>
      push({ title: "Follow-up question failed", description: err.message, variant: "error" }),
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = question.trim()
    if (!trimmed || askFollowup.isPending) return
    askFollowup.mutate(trimmed)
  }

  return (
    <div className="mt-3 flex flex-col gap-2 border-t border-border pt-3">
      {isLoading && (
        <div className="flex flex-col gap-1.5" aria-busy="true" aria-live="polite">
          <Skeleton className="h-3 w-2/3" />
        </div>
      )}
      {!isLoading && error && (
        <p className="text-xs text-destructive">Couldn't load follow-up history.</p>
      )}
      {!isLoading && !error && (followups?.length ?? 0) > 0 && (
        <ol className="flex flex-col gap-2.5">
          {followups!.map((f) => (
            <li key={f.id} className="text-xs">
              <div className="flex items-start gap-1.5 font-medium">
                <MessageCircleQuestion className="h-3.5 w-3.5 mt-0.5 shrink-0 text-muted-foreground" />
                <span>{f.question}</span>
              </div>
              <p className="mt-1 pl-5 text-muted-foreground leading-relaxed">{f.answer}</p>
              <span className="pl-5 text-[10px] text-muted-foreground/70">{formatTime(f.created_at)}</span>
            </li>
          ))}
        </ol>
      )}

      <form onSubmit={handleSubmit} className="flex items-center gap-2">
        <Input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask a follow-up, e.g. why wasn't this late-stage?"
          disabled={askFollowup.isPending}
          className="h-8 text-xs"
          aria-label="Ask a follow-up question about this agent's reasoning"
        />
        <Button
          type="submit"
          size="sm"
          variant="outline"
          disabled={!question.trim() || askFollowup.isPending}
          className="h-8 shrink-0 px-2.5"
        >
          {askFollowup.isPending ? (
            "Thinking..."
          ) : (
            <>
              <Send className="h-3.5 w-3.5" />
              Ask
            </>
          )}
        </Button>
      </form>
    </div>
  )
}
