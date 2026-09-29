output "instance_id" {
  description = "EC2 instance id"
  value       = aws_instance.app.id
}

output "public_ip" {
  description = "Elastic IP of the app box"
  value       = aws_eip.app.public_ip
}

output "url" {
  description = "Always-on gateway URL"
  value       = "http://${aws_eip.app.public_ip}:3002"
}

output "health_url" {
  value = "http://${aws_eip.app.public_ip}:3002/health"
}

output "ssh_command" {
  description = "SSH access (when an ssh_public_key was provided)"
  value       = var.ssh_public_key != "" ? "ssh -i <your-key> ec2-user@${aws_eip.app.public_ip}" : "no ssh key configured - use SSM Session Manager"
}