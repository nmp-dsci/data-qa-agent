# --------------------------------------------------------------------------
# Cheap hardening (s12 Phase E): CloudWatch alarms → SNS email.
# ~$0.10/alarm/month; SNS email is free. Two topics because alarm actions
# must target a topic in the alarm's own region, and AWS billing metrics
# only exist in us-east-1.
#
# NOTE: the email subscriptions require a one-time click on the confirmation
# email AWS sends. The billing metric additionally requires "Receive Billing
# Alerts" to be enabled once in Billing → Preferences.
# --------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_sns_topic" "alerts_use1" {
  provider = aws.use1
  name     = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email_use1" {
  provider  = aws.use1
  topic_arn = aws_sns_topic.alerts_use1.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ---- Monthly bill guard -----------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "billing" {
  provider            = aws.use1
  alarm_name          = "${local.name}-billing-over-${var.billing_alarm_usd}usd"
  alarm_description   = "Estimated month-to-date AWS charges crossed ${var.billing_alarm_usd} USD."
  namespace           = "AWS/Billing"
  metric_name         = "EstimatedCharges"
  dimensions          = { Currency = "USD" }
  statistic           = "Maximum"
  period              = 21600 # billing updates ~every 6h
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.billing_alarm_usd
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts_use1.arn]
}

# ---- Service error alarms ---------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "backend_5xx" {
  alarm_name        = "${local.name}-backend-api-5xx"
  alarm_description = "backend-api returned 5 or more 5xx responses in 5 minutes."
  namespace         = "AWS/AppRunner"
  metric_name       = "5xxStatusResponses"
  dimensions = {
    ServiceName = aws_apprunner_service.backend_api.service_name
    ServiceID   = aws_apprunner_service.backend_api.service_id
  }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "agent_5xx" {
  alarm_name        = "${local.name}-data-agent-5xx"
  alarm_description = "data-agent returned 5 or more 5xx responses in 5 minutes."
  namespace         = "AWS/AppRunner"
  metric_name       = "5xxStatusResponses"
  dimensions = {
    ServiceName = aws_apprunner_service.data_agent.service_name
    ServiceID   = aws_apprunner_service.data_agent.service_id
  }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
