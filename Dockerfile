FROM python:3-slim-bullseye
SHELL ["/bin/bash", "-c"]

# Add Tini
ENV TINI_VERSION v0.19.0
ADD https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini /tini
RUN chmod +x /tini

# Copy source into image
COPY . /opt/discord-pinup
WORKDIR /opt/discord-pinup

# Install build essentials
RUN apt update && \
    apt -y install --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/* && \
    apt clean

# Install python requirements
RUN pip install -r requirements.txt

# Run the server
ENTRYPOINT ["/tini", "-v", "--", "./docker-entrypoint.sh"]
