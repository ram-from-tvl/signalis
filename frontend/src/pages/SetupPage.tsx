import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { campaignsApi, personasApi, solutionsApi } from "@/api/endpoints"
import type { CampaignDetail, Persona, PersonaInput, Solution, SolutionInput } from "@/types/api"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { KeyValueBuilder } from "@/components/ui/key-value-builder"
import { useToast } from "@/components/ui/toast-context"
import { cn, parseUtcTimestamp } from "@/lib/utils"
import { Check, Plus, Star, Users } from "lucide-react"

const CHANNEL_OPTIONS = ["email", "linkedin", "phone", "events"]

function formatSavedAt(iso: string) {
  return parseUtcTimestamp(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

function usePersonaForm(existing?: Persona) {
  const [form, setForm] = useState<PersonaInput>({
    role: existing?.role ?? "",
    seniority: existing?.seniority ?? "",
    industry: existing?.industry ?? "",
    company_size_band: existing?.company_size_band ?? "",
    geography: existing?.geography ?? "",
    custom_traits: existing?.custom_traits ?? {},
  })
  return { form, setForm }
}

// Campaigns list on the left, so a GTM team running several targeting
// configs at once (e.g. "CTOs — Q3 platform push" and "VP Marketing —
// enterprise upsell") can see and switch between them — instead of the
// whole pipeline sharing one global "active" persona/solution.
function CampaignSwitcher({
  campaigns,
  isLoading,
  isError,
  selectedId,
  onSelect,
  onNew,
}: {
  campaigns: CampaignDetail[] | undefined
  isLoading: boolean
  isError: boolean
  selectedId: string | null
  onSelect: (id: string) => void
  onNew: () => void
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-3">
        <div>
          <CardTitle>Campaigns</CardTitle>
          <CardDescription>Each has its own persona and solution.</CardDescription>
        </div>
        <Button size="sm" variant="outline" onClick={onNew} disabled={isError}>
          <Plus className="h-3.5 w-3.5" /> New
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {isLoading && (
          <div className="flex flex-col gap-2" aria-busy="true" aria-live="polite">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        )}
        {isError && (
          <p className="text-sm text-destructive">Couldn't load campaigns.</p>
        )}
        {!isLoading && !isError && (campaigns?.length ?? 0) === 0 && (
          <p className="text-sm text-muted-foreground">
            No campaigns yet — create one to start scoring leads against a real persona.
          </p>
        )}
        {campaigns?.map((c) => (
          <button
            key={c.id}
            type="button"
            onClick={() => onSelect(c.id)}
            className={cn(
              "flex items-start justify-between gap-2 rounded-lg border p-3 text-left transition-colors",
              selectedId === c.id
                ? "border-accent bg-accent/5"
                : "border-border hover:border-accent/40 hover:bg-secondary/40"
            )}
          >
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <p className="text-sm font-semibold truncate">{c.name}</p>
                {c.is_default && (
                  <Star className="h-3 w-3 shrink-0 fill-warning text-warning" aria-label="Default campaign" />
                )}
              </div>
              <p className="text-xs text-muted-foreground truncate mt-0.5">
                {c.persona.role || "Unnamed persona"} · {c.solution.name || "Unnamed solution"}
              </p>
            </div>
            <Badge variant="secondary" className="shrink-0 gap-1">
              <Users className="h-3 w-3" /> {c.lead_count}
            </Badge>
          </button>
        ))}
      </CardContent>
    </Card>
  )
}

// The persona + solution forms for whichever campaign is selected. A
// "new campaign" is a persona+solution+campaign created together in one
// flow (three POSTs, only committed once all three succeed at the UI
// level — no partial "persona created but no campaign wraps it" state
// left behind on a normal save).
function CampaignEditor({
  campaign,
  isNew,
  onSaved,
}: {
  campaign: CampaignDetail | null
  isNew: boolean
  onSaved: (campaignId: string) => void
}) {
  const queryClient = useQueryClient()
  const { push } = useToast()

  const [campaignName, setCampaignName] = useState(campaign?.name ?? "")
  const { form: personaForm, setForm: setPersonaForm } = usePersonaForm(campaign?.persona)
  const [customTraits, setCustomTraits] = useState<Record<string, unknown>>(
    campaign?.persona.custom_traits ?? {}
  )
  const [solutionForm, setSolutionForm] = useState<SolutionInput>({
    name: campaign?.solution.name ?? "",
    problem_solved: campaign?.solution.problem_solved ?? "",
    value_props: campaign?.solution.value_props ?? [],
    differentiators: campaign?.solution.differentiators ?? [],
    channels: campaign?.solution.channels ?? ["email"],
  })
  const [valuePropsText, setValuePropsText] = useState((campaign?.solution.value_props ?? []).join("\n"))
  const [differentiatorsText, setDifferentiatorsText] = useState(
    (campaign?.solution.differentiators ?? []).join("\n")
  )

  // Re-seed local form state whenever the selected campaign changes (not
  // on every keystroke — campaign?.id is the actual dependency).
  useEffect(() => {
    setCampaignName(campaign?.name ?? "")
    setPersonaForm({
      role: campaign?.persona.role ?? "",
      seniority: campaign?.persona.seniority ?? "",
      industry: campaign?.persona.industry ?? "",
      company_size_band: campaign?.persona.company_size_band ?? "",
      geography: campaign?.persona.geography ?? "",
      custom_traits: campaign?.persona.custom_traits ?? {},
    })
    setCustomTraits(campaign?.persona.custom_traits ?? {})
    setSolutionForm({
      name: campaign?.solution.name ?? "",
      problem_solved: campaign?.solution.problem_solved ?? "",
      value_props: campaign?.solution.value_props ?? [],
      differentiators: campaign?.solution.differentiators ?? [],
      channels: campaign?.solution.channels ?? ["email"],
    })
    setValuePropsText((campaign?.solution.value_props ?? []).join("\n"))
    setDifferentiatorsText((campaign?.solution.differentiators ?? []).join("\n"))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaign?.id])

  const saveMutation = useMutation({
    mutationFn: async () => {
      const personaPayload = { ...personaForm, custom_traits: customTraits }
      const solutionPayload: SolutionInput = {
        ...solutionForm,
        value_props: valuePropsText.split("\n").map((s) => s.trim()).filter(Boolean),
        differentiators: differentiatorsText.split("\n").map((s) => s.trim()).filter(Boolean),
      }

      let persona: Persona
      let solution: Solution
      if (isNew || !campaign) {
        persona = await personasApi.create(personaPayload)
        solution = await solutionsApi.create(solutionPayload)
        const created = await campaignsApi.create({
          name: campaignName || "Untitled campaign",
          persona_id: persona.id,
          solution_id: solution.id,
        })
        return created.id
      }

      await personasApi.update(campaign.persona.id, personaPayload)
      await solutionsApi.update(campaign.solution.id, solutionPayload)
      if (campaignName !== campaign.name) {
        await campaignsApi.update(campaign.id, {
          name: campaignName,
          persona_id: campaign.persona.id,
          solution_id: campaign.solution.id,
          is_default: campaign.is_default,
        })
      }
      return campaign.id
    },
    onSuccess: (campaignId) => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] })
      queryClient.invalidateQueries({ queryKey: ["personas"] })
      queryClient.invalidateQueries({ queryKey: ["solutions"] })
      push({ title: isNew ? "Campaign created" : "Campaign saved", variant: "success" })
      onSaved(campaignId)
    },
    onError: (err: Error) =>
      push({ title: "Could not save campaign", description: err.message, variant: "error" }),
  })

  const setDefaultMutation = useMutation({
    mutationFn: () => {
      if (!campaign) throw new Error("No campaign selected")
      return campaignsApi.update(campaign.id, {
        name: campaign.name,
        persona_id: campaign.persona.id,
        solution_id: campaign.solution.id,
        is_default: true,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] })
      push({ title: "Set as default campaign", variant: "success" })
    },
    onError: (err: Error) =>
      push({ title: "Could not set default", description: err.message, variant: "error" }),
  })

  const toggleChannel = (channel: string) => {
    setSolutionForm((prev) => ({
      ...prev,
      channels: prev.channels.includes(channel)
        ? prev.channels.filter((c) => c !== channel)
        : [...prev.channels, channel],
    }))
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 py-4">
          <div className="flex-1 min-w-[12rem]">
            <Label htmlFor="campaign-name" className="sr-only">
              Campaign name
            </Label>
            <Input
              id="campaign-name"
              value={campaignName}
              onChange={(e) => setCampaignName(e.target.value)}
              placeholder="e.g. CTOs — Q3 platform push"
              className="font-heading text-base font-semibold"
            />
          </div>
          {campaign && !campaign.is_default && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setDefaultMutation.mutate()}
              disabled={setDefaultMutation.isPending}
            >
              <Star className="h-3.5 w-3.5" />
              {setDefaultMutation.isPending ? "Setting..." : "Set as default"}
            </Button>
          )}
          {campaign?.is_default && (
            <Badge variant="warning" className="gap-1">
              <Star className="h-3 w-3 fill-current" /> Default — leads without a campaign use this one
            </Badge>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Target Persona</CardTitle>
            <CardDescription>Role, seniority, industry, and firmographic fit criteria.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="role">Role / Title</Label>
                <Input
                  id="role"
                  value={personaForm.role}
                  onChange={(e) => setPersonaForm((p) => ({ ...p, role: e.target.value }))}
                  placeholder="VP of Sales"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="seniority">Seniority</Label>
                <Input
                  id="seniority"
                  value={personaForm.seniority}
                  onChange={(e) => setPersonaForm((p) => ({ ...p, seniority: e.target.value }))}
                  placeholder="VP / Director"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="industry">Industry</Label>
                <Input
                  id="industry"
                  value={personaForm.industry}
                  onChange={(e) => setPersonaForm((p) => ({ ...p, industry: e.target.value }))}
                  placeholder="Logistics, SaaS"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="size">Company Size Band</Label>
                <Input
                  id="size"
                  value={personaForm.company_size_band}
                  onChange={(e) => setPersonaForm((p) => ({ ...p, company_size_band: e.target.value }))}
                  placeholder="51-1000"
                />
              </div>
              <div className="flex flex-col gap-1.5 col-span-2">
                <Label htmlFor="geo">Geography</Label>
                <Input
                  id="geo"
                  value={personaForm.geography}
                  onChange={(e) => setPersonaForm((p) => ({ ...p, geography: e.target.value }))}
                  placeholder="Global, English-speaking preferred"
                />
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Custom Traits</Label>
              <p className="text-xs text-muted-foreground -mt-1">
                Any extra fit criteria the Persona Fit Agent should weigh, beyond the fields above.
              </p>
              <KeyValueBuilder value={customTraits} onChange={setCustomTraits} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Solution Definition</CardTitle>
            <CardDescription>What you sell, why it matters, and where it wins.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="sname">Solution Name</Label>
              <Input
                id="sname"
                value={solutionForm.name}
                onChange={(e) => setSolutionForm((p) => ({ ...p, name: e.target.value }))}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="problem">Problem Solved</Label>
              <Textarea
                id="problem"
                rows={2}
                value={solutionForm.problem_solved}
                onChange={(e) => setSolutionForm((p) => ({ ...p, problem_solved: e.target.value }))}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="valueprops">Value Props (one per line)</Label>
              <Textarea
                id="valueprops"
                rows={3}
                value={valuePropsText}
                onChange={(e) => setValuePropsText(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="diff">Differentiators (one per line)</Label>
              <Textarea
                id="diff"
                rows={3}
                value={differentiatorsText}
                onChange={(e) => setDifferentiatorsText(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label>Target Channels</Label>
              <div className="flex flex-wrap gap-2">
                {CHANNEL_OPTIONS.map((channel) => {
                  const checked = solutionForm.channels.includes(channel)
                  return (
                    <button
                      key={channel}
                      type="button"
                      role="checkbox"
                      aria-checked={checked}
                      onClick={() => toggleChannel(channel)}
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold capitalize cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        checked
                          ? "border-transparent bg-accent text-accent-foreground"
                          : "border-border text-foreground hover:bg-secondary"
                      )}
                    >
                      {checked && <Check className="h-3 w-3" />}
                      {channel}
                    </button>
                  )
                })}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="flex items-center gap-3">
        <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
          {saveMutation.isPending ? "Saving..." : isNew ? "Create Campaign" : "Save Campaign"}
        </Button>
        {campaign && !isNew && (
          <p className="text-xs text-muted-foreground">
            Persona saved {formatSavedAt(campaign.persona.created_at)} · Solution saved{" "}
            {formatSavedAt(campaign.solution.created_at)}
          </p>
        )}
      </div>
    </div>
  )
}

export function SetupPage() {
  const {
    data: campaigns,
    isLoading,
    isError,
    error: campaignsError,
    refetch: refetchCampaigns,
  } = useQuery({
    queryKey: ["campaigns"],
    queryFn: campaignsApi.list,
  })
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [creatingNew, setCreatingNew] = useState(false)

  // Default the selection to the default campaign (or the first one) once
  // campaigns load, but only if nothing is selected yet — don't fight a
  // user's own in-progress selection or "new campaign" draft.
  useEffect(() => {
    if (selectedId || creatingNew || !campaigns?.length) return
    const defaultCampaign = campaigns.find((c) => c.is_default) ?? campaigns[0]
    setSelectedId(defaultCampaign.id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaigns])

  const selectedCampaign = campaigns?.find((c) => c.id === selectedId) ?? null

  return (
    <div className="flex flex-col gap-6 pb-12">
      <div>
        <h1 className="font-heading text-2xl font-bold">Campaigns</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Each campaign pairs a target persona with a solution — leads are scored against the
          campaign they're assigned to, not one shared configuration.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[18rem_1fr] items-start">
        <CampaignSwitcher
          campaigns={campaigns}
          isLoading={isLoading}
          isError={isError}
          selectedId={creatingNew ? null : selectedId}
          onSelect={(id) => {
            setCreatingNew(false)
            setSelectedId(id)
          }}
          onNew={() => setCreatingNew(true)}
        />

        {isError ? (
          <Card>
            <CardContent className="py-12 flex flex-col items-center text-center gap-3">
              <p className="text-sm text-muted-foreground max-w-sm">
                Couldn't load campaigns.{" "}
                {campaignsError instanceof Error ? campaignsError.message : "Something went wrong contacting the server."}
              </p>
              <Button variant="outline" onClick={() => refetchCampaigns()}>
                Try again
              </Button>
            </CardContent>
          </Card>
        ) : creatingNew ? (
          <CampaignEditor
            campaign={null}
            isNew
            onSaved={(id) => {
              setCreatingNew(false)
              setSelectedId(id)
            }}
          />
        ) : selectedCampaign ? (
          <CampaignEditor
            campaign={selectedCampaign}
            isNew={false}
            onSaved={(id) => setSelectedId(id)}
          />
        ) : !isLoading ? (
          <Card>
            <CardContent className="py-12 text-center text-sm text-muted-foreground">
              Create your first campaign to define who the Persona Fit Agent scores leads
              against and what the Outreach Planner Agent positions in every message.
            </CardContent>
          </Card>
        ) : (
          <Skeleton className="h-96 w-full" />
        )}
      </div>
    </div>
  )
}
