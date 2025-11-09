require('dotenv').config();
const { MezonClient } = require('mezon-sdk');
const handleInviteAgent = require('./handlers/invite_agent');
const handleRemoveAgent = require('./handlers/remove_agent');

const BOT_TOKEN = process.env.APPLICATION_TOKEN;
const BOT_ID = process.env.APPLICATION_ID;
const account = {
    appid: BOT_ID,  
    token: BOT_TOKEN
};

async function main() {

  const client = new MezonClient({ botId: BOT_ID, token: BOT_TOKEN }); 
  await client.login();

  client.onVoiceJoinedEvent(async (event) => {
    try {
      return handleInviteAgent(client, event, account);
    } catch (err) {
      console.error("ohnooo", err);
    }
  });

  // client.onVoiceLeavedEvent(async (event) => {
  //   try {
  //     return handleRemoveAgent(client, event, account);
  //   } catch (err) {
  //     console.error("ohnooo", err);
  //   }
  // });


};


main()
  .then(() => console.log("Bot is running"))
  .catch(console.error);

  