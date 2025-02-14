# ... other Dockerfile steps ...

# Copy and make the startup script executable
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Use the script as the entrypoint
CMD ["/app/start.sh"]