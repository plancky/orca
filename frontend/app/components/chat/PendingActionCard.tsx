import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "~/components/ui/alert-dialog";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "~/components/ui/card";
import type { PendingAction } from "~/lib/api/domain";
import type { Decision } from "~/lib/chat/useConfirmAction";

export function PendingActionCard({
  action,
  onDecision,
  disabled,
}: {
  action: PendingAction;
  onDecision: (actionId: string, decision: Decision) => void;
  disabled?: boolean;
}) {
  return (
    <Card className="border-amber-500/50">
      <CardHeader>
        <CardTitle className="text-sm">Confirm: {action.tool}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {action.preview ? (
          <p className="text-sm text-muted-foreground">{action.preview}</p>
        ) : null}
        <div className="flex gap-2">
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button size="sm" disabled={disabled}>
                Approve
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Approve this action?</AlertDialogTitle>
                <AlertDialogDescription>
                  {action.tool} will run
                  {action.preview ? `: ${action.preview}` : ""}. This performs a
                  real write.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  onClick={() => onDecision(action.action_id, "approved")}
                >
                  Approve
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
          <Button
            size="sm"
            variant="outline"
            disabled={disabled}
            onClick={() => onDecision(action.action_id, "denied")}
          >
            Deny
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
