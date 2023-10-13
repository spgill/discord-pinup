# stdlib imports
import typing

# vendor imports
import discord
import typer


MAX_DESCRIPTION_LENGTH = 500

EMOJI_PIN = "📌"
EMOJI_REMOVE = "🗑️"

CONTROL_EMOJIS = [EMOJI_PIN, EMOJI_REMOVE]


# Dictionary for storing config values
class PinupConfig(typing.TypedDict):
    channel_map: dict[int, int]
    history_limit: int


config: PinupConfig

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


def is_text_channel(
    channel: discord.abc.GuildChannel
    | discord.Thread
    | discord.abc.PrivateChannel
    | None,
) -> typing.TypeGuard[discord.TextChannel]:
    return isinstance(channel, discord.TextChannel)


# Print message when logged in
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")


async def create_message_preview(message: discord.Message) -> discord.Embed:
    author = message.author

    description = message.clean_content
    if len(description) > MAX_DESCRIPTION_LENGTH:
        description = description[:MAX_DESCRIPTION_LENGTH] + "..."

    preview = discord.Embed(
        title="Jump to pinned message",
        description=description,
        timestamp=message.created_at,
        url=message.jump_url,
        color=discord.Color.blurple(),
    )

    # Copy over attachments
    if len(message.attachments) > 0:
        preview.set_image(url=message.attachments[0].url)

        if len(message.attachments) > 1:
            preview.add_field(
                name="Attachments",
                value=", ".join(
                    att.filename for att in message.attachments[1:]
                ),
            )

    # Set the footer
    preview.set_footer(
        icon_url=author.display_avatar,
        text=f"{author.name} in #{message.channel.name}",
    )

    return preview


# Remove messages sent to the pins channel and DM the author
@client.event
async def on_message(message: discord.Message):
    global config
    channel_map = config["channel_map"]
    guild_id = message.guild.id if message.guild else -1

    if guild_id in channel_map:
        # If this is a notification message that a message was pinned, delete it
        if message.type == discord.MessageType.pins_add:
            await message.delete()

        # Else, if this is a message in the pins channel and the author is not self
        # then delete it
        if (
            channel_map.get(guild_id, -1) == message.channel.id
            and message.channel.type
            not in [
                discord.ChannelType.public_thread,
                discord.ChannelType.private_thread,
            ]
            and message.author.id != getattr(client.user, "id", None)
        ):
            await message.delete()
            await message.author.send(
                "Please don't send messages in the pins channel! Try starting a thread instead. 🫠"
            )


@client.event
async def on_audit_log_entry_create(entry: discord.AuditLogEntry):
    global config
    channel_map = config["channel_map"]

    if entry.action == discord.AuditLogAction.message_pin:
        assert isinstance(
            entry.extra, discord.audit_logs._AuditLogProxyPinAction
        )

        guild = entry.guild

        if pin_channel_id := channel_map.get(guild.id, None):
            channel = entry.extra.channel

            if not isinstance(channel, discord.TextChannel):
                print("Invalid channel type")
                return

            message = await channel.fetch_message(entry.extra.message_id)
            pin_channel = guild.get_channel(pin_channel_id)

            if not isinstance(pin_channel, discord.TextChannel):
                print("Invalid channel type")
                return

            # Generate a preview of the message and send it to the pin channel
            preview = await create_message_preview(message)
            await pin_channel.send(embeds=[preview, *message.embeds])

            # Remove the original pin
            await message.unpin()

            # Add the reaction emojis
            await message.add_reaction(EMOJI_PIN)
            await message.add_reaction(EMOJI_REMOVE)


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    global config
    channel_map = config["channel_map"]
    member = payload.member
    emoji = payload.emoji

    if (
        # if the guild is tracked
        payload.guild_id in channel_map
        # AND this is one of the controlled emojis
        and str(emoji) in CONTROL_EMOJIS
        # AND the channel is not the pins channel
        and payload.channel_id != channel_map[payload.guild_id]
        # AND the member is known
        and member
        # AND the member adding the reaction is not the bot itself
        and member.id != (client.user and client.user.id)
    ):
        # Start by resolving the channel for both the origin message and the pins
        pins_channel_id = channel_map[payload.guild_id]
        origin_channel_id = payload.channel_id
        if is_text_channel(
            pins_channel := client.get_channel(pins_channel_id)
        ) and is_text_channel(
            origin_channel := client.get_channel(origin_channel_id)
        ):
            # Get the origin message
            origin_message = await origin_channel.fetch_message(
                payload.message_id
            )

            has_pin = False
            for reaction in origin_message.reactions:
                if str(reaction.emoji) == EMOJI_PIN and reaction.me:
                    has_pin = True
                    break

            # Check user permissions
            origin_channel_permissions = origin_channel.permissions_for(member)
            has_perm = (
                member.guild_permissions.administrator
                or origin_channel_permissions.manage_messages
            )

            # If the user doesn't have permissions, remove their reaction and slide into their DMs
            if not has_perm:
                await origin_message.remove_reaction(emoji, member)
                await member.send("You don't have permission to do that! 🚫")
                return

            # If the pin emoji is being added, then the user is trying to pin this message
            if emoji.name == EMOJI_PIN:
                # If the message already has a pin, reject this
                if has_pin:
                    await origin_message.remove_reaction(emoji, member)
                    await member.send(
                        "That message has already been pinned! 😱"
                    )
                    return

                # Clear any existing reactions using control emojis
                [
                    await origin_message.clear_reaction(e)
                    for e in CONTROL_EMOJIS
                ]

                # Generate a preview of the message and send it to the pin channel
                preview = await create_message_preview(origin_message)
                await pins_channel.send(
                    embeds=[preview, *origin_message.embeds]
                )

                # Add the control reaction emojis in order
                [await origin_message.add_reaction(e) for e in CONTROL_EMOJIS]

            # If this is the remove emoji being added, then the goal is to delete the pin
            elif emoji.name == EMOJI_REMOVE:
                # Search the pin channel for the pin of this message
                found_pin_message: discord.Message | None = None
                async for message in pins_channel.history(
                    limit=config["history_limit"]
                ):
                    if len(message.embeds) > 0:
                        first_embed = message.embeds[0]
                        if first_embed.url == origin_message.jump_url:
                            found_pin_message = message
                            break

                # If the message was found in the pins channel, delete it
                if found_pin_message:
                    await found_pin_message.delete()

                # Else, add an emoji to the original pinned message
                else:
                    await origin_message.remove_reaction(emoji, member)
                    await member.send(
                        "I'm sorry, but I couldn't find the pin related to that message. 🫣"
                    )

                # Regardless, clean up reactions on the message
                await origin_message.clear_reaction(EMOJI_PIN)
                await origin_message.clear_reaction(EMOJI_REMOVE)


def cli(
    token: typing.Annotated[
        str, typer.Argument(envvar="DISCORD_TOKEN", help="Discord API token.")
    ],
    channel_map: typing.Annotated[
        str,
        typer.Argument(
            envvar="DISCORD_CHANNEL_MAP",
            help="Mapping of Server/Guild ids to pin channels, comma-delimited.",
        ),
    ],
    history_limit: typing.Annotated[
        int,
        typer.Option(
            "-h",
            envvar="DISCORD_HISTORY_LIMIT",
            help="Maximum number of messages to iterate through when searching a channel for a specific pin.",
        ),
    ] = 1000,
):
    # Set the config object
    global config
    config = {
        "channel_map": {
            int(member.split(":")[0]): int(member.split(":")[1])
            for member in channel_map.split(",")
        },
        "history_limit": history_limit,
    }

    client.run(token)


def main():
    typer.run(cli)
