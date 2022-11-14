# stdlib imports
import typing

# vendor imports
import discord
import motor.motor_asyncio
import typer


MAX_DESCRIPTION_LENGTH = 500
PIN_EMOJI = "📌"


# Dictionary for storing config values
class PinupConfig(typing.TypedDict):
    channelMap: dict[int, int]
    collection: str


intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


# Print message when logged in
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")


async def createMessagePreview(
    pinner: discord.Member, message: discord.Message
) -> discord.Embed:
    author = message.author

    description = message.clean_content
    if len(description) > MAX_DESCRIPTION_LENGTH:
        description = description[:MAX_DESCRIPTION_LENGTH] + "..."

    preview = discord.Embed(
        title=f"Message pinned by {pinner.name}#{pinner.discriminator}",
        description=description,
        timestamp=message.created_at,
        url=message.jump_url,
        color=discord.Color.red(),
    )

    # Copy over attachments
    if len(message.attachments) > 0:
        preview.set_image(url=message.attachments[0].url)

        if len(message.attachments) > 1:
            preview.add_field(
                name="Attachments",
                value=", ".join(att.filename for att in message.attachments[1:]),
            )

    # Set the footer
    preview.set_footer(
        icon_url=author.display_avatar,
        text=f"{author.name}#{author.discriminator} in #{message.channel.name}",
    )

    return preview


# React to pins being added
@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    config = typing.cast(PinupConfig, client.config)
    channelMap = config["channelMap"]
    mongo = typing.cast(motor.motor_asyncio.AsyncIOMotorClient, client.mongo)
    db = mongo.get_default_database()
    collection = db[config["collection"]]

    if payload.guild_id in channelMap and payload.emoji.name == PIN_EMOJI:
        pinChannelId = channelMap[payload.guild_id]
        pinChannel = client.get_channel(pinChannelId)

        # Resolve the message ID to an object
        channel = client.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)

        # Can't pin a pin
        if payload.channel_id == pinChannelId:
            await message.remove_reaction(PIN_EMOJI, payload.member)
            await payload.member.send("Please don't pin another pin! 🫠")
            return

        # Check if the message has been pinned before
        if (
            await collection.find_one(
                {
                    "guild_id": payload.guild_id,
                    "message_id": payload.message_id,
                }
            )
        ) is not None:
            await message.remove_reaction(PIN_EMOJI, payload.member)
            await payload.member.send("This message has already been pinned! 🫠")
            return

        print(f"Creating pin for {message.id} in {payload.guild_id}")

        preview = await createMessagePreview(payload.member, message)

        pin = await pinChannel.send(embeds=[preview, *message.embeds])

        # Create a new database record for this message
        collection.insert_one(
            {
                "guild_id": payload.guild_id,
                "message_id": payload.message_id,
                "pin_id": pin.id,
                "pinner": payload.user_id,
            }
        )


# React to pins being removed
@client.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    config = typing.cast(PinupConfig, client.config)
    channelMap = config["channelMap"]
    mongo = typing.cast(motor.motor_asyncio.AsyncIOMotorClient, client.mongo)
    db = mongo.get_default_database()
    collection = db[config["collection"]]

    if payload.guild_id in channelMap and payload.emoji.name == PIN_EMOJI:
        pinChannelId = channelMap[payload.guild_id]
        pinChannel = client.get_channel(pinChannelId)

        # Resolve the message ID to an object
        channel = client.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)

        print(f"Attempting to remove pin for {message.id} in {payload.guild_id}")

        # Try to locate the database doc for the message
        pinDoc = await collection.find_one(
            {
                "guild_id": payload.guild_id,
                "message_id": payload.message_id,
                "pinner": payload.user_id,
            }
        )
        if pinDoc is not None:
            try:
                pinMessage = await pinChannel.fetch_message(pinDoc["pin_id"])
                await pinMessage.delete()
            except:
                pass
            collection.delete_one({"_id": pinDoc["_id"]})


# React to messages sent in the pins channel
@client.event
async def on_message(message: discord.Message):
    if message.author.id == client.user.id:
        return
    config = typing.cast(PinupConfig, client.config)
    channelMap = config["channelMap"]
    guildId = message.guild.id if message.guild else -1
    if channelMap.get(guildId, -1) == message.channel.id:
        await message.delete()
        await message.author.send(
            "Please don't send messages in the pins channel! Try starting a thread instead. 🫠"
        )


def cli(
    token: str = typer.Argument(..., envvar="DISCORD_TOKEN"),
    channel_map: str = typer.Argument(..., envvar="DISCORD_CHANNEL_MAP"),
    mongodb_uri: str = typer.Argument(..., envvar="MONGODB_URI"),
    mongodb_collection: str = typer.Argument("pins", envvar="MONGODB_COLLECTION"),
):
    # Set the config object
    config: PinupConfig = {
        "channelMap": {
            int(member.split(":")[0]): int(member.split(":")[1])
            for member in channel_map.split(",")
        },
        "collection": mongodb_collection,
    }
    client.config = config

    # Open the mongodb connection
    client.mongo = motor.motor_asyncio.AsyncIOMotorClient(mongodb_uri)

    client.run(token)


def main():
    typer.run(cli)
