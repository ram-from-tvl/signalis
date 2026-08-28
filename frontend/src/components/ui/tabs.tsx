import * as React from "react"
import * as TabsPrimitive from "@radix-ui/react-tabs"
import { motion } from "motion/react"
import { cn } from "@/lib/utils"

const Tabs = TabsPrimitive.Root

// Scoped per <TabsList>, not per-app: a unique id lets several independent
// Tabs instances share the "active-pill" motion vocabulary without their
// layoutId animations bleeding into each other.
const TabsListIdContext = React.createContext<string | null>(null)

const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => {
  const listId = React.useId()
  return (
    <TabsListIdContext.Provider value={listId}>
      <TabsPrimitive.List
        ref={ref}
        className={cn(
          "inline-flex h-10 items-center justify-start gap-1 rounded-lg bg-secondary p-1 text-secondary-foreground",
          className
        )}
        {...props}
      />
    </TabsListIdContext.Provider>
  )
})
TabsList.displayName = TabsPrimitive.List.displayName

const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger> & { active?: boolean }
>(({ className, children, active, ...props }, ref) => {
  const listId = React.useContext(TabsListIdContext)
  return (
    <TabsPrimitive.Trigger
      ref={ref}
      className={cn(
        "relative inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 data-[state=active]:text-foreground text-muted-foreground",
        className
      )}
      {...props}
    >
      {active && (
        <motion.span
          layoutId={listId ? `tabs-pill-${listId}` : undefined}
          className="absolute inset-0 rounded-md bg-card shadow-subtle"
          transition={{ type: "spring", stiffness: 500, damping: 40 }}
        />
      )}
      <span className="relative z-10">{children}</span>
    </TabsPrimitive.Trigger>
  )
})
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName

const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn(
      "mt-4 focus-visible:outline-none animate-fade-in",
      className
    )}
    {...props}
  />
))
TabsContent.displayName = TabsPrimitive.Content.displayName

export { Tabs, TabsList, TabsTrigger, TabsContent }
