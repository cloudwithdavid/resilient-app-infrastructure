terraform {
  backend "s3" {
    bucket              = "cp1-tfstate-963348789536"
    key                 = "bootstrap/terraform.tfstate"
    region              = "us-east-2"
    allowed_account_ids = ["963348789536"]
    use_lockfile        = true
  }
}
