import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { personasApi, solutionsApi } from "@/api/endpoints"
import type { Persona, PersonaInput, SolutionInput } from "@/types/api"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Badge } from "@/components/ui/badge"
import { useToast } from "@/components/ui/toast-context"

const CHANNEL_OPTIONS = ["email", "linkedin", "phone", "events"]

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

export function SetupPage() {
  const queryClient = useQueryClient()
  const { push } = useToast()

  const { data: personas } = useQuery({ queryKey: ["personas"], queryFn: personasApi.list })
  const { data: solutions } = useQuery({ queryKey: ["solutions"], queryFn: solutionsApi.list })

  const currentPersona = personas?.[0]
  const currentSolution = solutions?.[0]

  const { form: personaForm, setForm: setPersonaForm } = usePersonaForm(currentPersona)
  const [customTraitsText, setCustomTraitsText] = useState(
    currentPersona ? JSON.stringify(currentPersona.custom_traits, null, 2) : "{}"
  )

  const [solutionForm, setSolutionForm] = useState<SolutionInput>({
    name: currentSolution?.name ?? "",
    problem_solved: currentSolution?.problem_solved ?? "",
    value_props: currentSolution?.value_props ?? [],
    differentiators: currentSolution?.differentiators ?? [],
    channels: currentSolution?.channels ?? ["email"],
  })
  const [valuePropsText, setValuePropsText] = useState(
    (currentSolution?.value_props ?? []).join("\n")
  )
  const [differentiatorsText, setDifferentiatorsText] = useState(
    (currentSolution?.differentiators ?? []).join("\n")
  )

  const personaMutation = useMutation({
    mutationFn: async () => {
      let customTraits: Record<string, unknown> = {}
      try {
        customTraits = customTraitsText.trim() ? JSON.parse(customTraitsText) : {}
      } catch {
        throw new Error("Custom traits must be valid JSON")
      }
      const payload = { ...personaForm, custom_traits: customTraits }
      return currentPersona
        ? personasApi.update(currentPersona.id, payload)
        : personasApi.create(payload)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["personas"] })
      push({ title: "Persona saved", variant: "success" })
    },
    onError: (err: Error) => push({ title: "Could not save persona", description: err.message, variant: "error" }),
  })

  const solutionMutation = useMutation({
    mutationFn: async () => {
      const payload: SolutionInput = {
        ...solutionForm,
        value_props: valuePropsText.split("\n").map((s) => s.trim()).filter(Boolean),
        differentiators: differentiatorsText.split("\n").map((s) => s.trim()).filter(Boolean),
      }
      return currentSolution
        ? solutionsApi.update(currentSolution.id, payload)
        : solutionsApi.create(payload)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["solutions"] })
      push({ title: "Solution saved", variant: "success" })
    },
    onError: (err: Error) => push({ title: "Could not save solution", description: err.message, variant: "error" }),
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
    <div className="flex flex-col gap-6 pb-12">
      <div>
        <h1 className="font-heading text-2xl font-bold">Persona & Solution</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Defines who the Persona Fit Agent scores leads against and what the
          Outreach Planner Agent positions in every message it drafts.
        </p>
      </div>

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
              <Label htmlFor="traits">Custom Traits (JSON)</Label>
              <Textarea
                id="traits"
                rows={4}
                className="font-mono text-xs"
                value={customTraitsText}
                onChange={(e) => setCustomTraitsText(e.target.value)}
              />
            </div>
            <Button
              onClick={() => personaMutation.mutate()}
              disabled={personaMutation.isPending}
              className="self-start"
            >
              {personaMutation.isPending ? "Saving..." : "Save Persona"}
            </Button>
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
              <div className="flex flex-wrap gap-4">
                {CHANNEL_OPTIONS.map((channel) => (
                  <label key={channel} className="flex items-center gap-2 text-sm capitalize cursor-pointer">
                    <Checkbox
                      checked={solutionForm.channels.includes(channel)}
                      onCheckedChange={() => toggleChannel(channel)}
                    />
                    {channel}
                  </label>
                ))}
              </div>
            </div>
            <Button
              onClick={() => solutionMutation.mutate()}
              disabled={solutionMutation.isPending}
              className="self-start"
            >
              {solutionMutation.isPending ? "Saving..." : "Save Solution"}
            </Button>
          </CardContent>
        </Card>
      </div>

      {(currentPersona || currentSolution) && (
        <Card>
          <CardHeader>
            <CardTitle>Active Configuration</CardTitle>
            <CardDescription>
              Used by every agent run going forward. The most recently saved persona and
              solution are treated as active.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {currentPersona && (
              <Badge variant="secondary">Persona: {currentPersona.role || "Unnamed"}</Badge>
            )}
            {currentSolution && (
              <Badge variant="secondary">Solution: {currentSolution.name || "Unnamed"}</Badge>
            )}
            {currentSolution?.channels.map((c) => (
              <Badge key={c} variant="outline" className="capitalize">
                {c}
              </Badge>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
