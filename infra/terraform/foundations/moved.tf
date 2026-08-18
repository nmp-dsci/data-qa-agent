# s38: adding `count = var.demo_mode ? 0 : 1` to these two resources changes
# their state address from the bare name to `[0]` even on deployments where
# demo_mode stays false and the count still evaluates to 1 — Terraform tracks
# resources by address, not by count value. Without these `moved` blocks, the
# next `terraform apply` on any existing non-demo deployment would plan a
# destroy-and-recreate of the live data-agent service and its 5xx alarm.
moved {
  from = aws_apprunner_service.data_agent
  to   = aws_apprunner_service.data_agent[0]
}

moved {
  from = aws_cloudwatch_metric_alarm.agent_5xx
  to   = aws_cloudwatch_metric_alarm.agent_5xx[0]
}
