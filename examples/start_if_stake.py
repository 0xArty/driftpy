import sys
sys.path.append('../src/')

from driftpy.constants.config import configs
from anchorpy import Provider
import json 
from anchorpy import Wallet
from solana.rpc.async_api import AsyncClient
from driftpy.clearing_house import ClearingHouse
from driftpy.accounts import *
from solana.keypair import Keypair

# todo: airdrop udsc + init account for any kp
# rn do it through UI 
from driftpy.clearing_house_user import ClearingHouseUser
from driftpy.constants.numeric_constants import AMM_RESERVE_PRECISION
from solana.rpc import commitment
import pprint

async def view_logs(
    sig: str,
    connection: AsyncClient
):
    connection._commitment = commitment.Confirmed 
    logs = ''
    try: 
        await connection.confirm_transaction(sig, commitment.Confirmed)
        logs = (await connection.get_transaction(sig))["result"]["meta"]["logMessages"]
    finally:
        connection._commitment = commitment.Processed 
    pprint.pprint(logs)

async def main(
    keypath, 
    env, 
    url, 
    spot_market_index,
    if_amount,
    operation,
):
    with open(keypath, 'r') as f: secret = json.load(f) 
    kp = Keypair.from_secret_key(bytes(secret))
    print('using public key:', kp.public_key)
    print('spot market:', spot_market_index)
    
    config = configs[env]
    wallet = Wallet(kp)
    connection = AsyncClient(url)
    provider = Provider(connection, wallet)

    from driftpy.constants.numeric_constants import QUOTE_PRECISION
    ch = ClearingHouse.from_config(config, provider)
    chu = ClearingHouseUser(ch)
    print(ch.program_id)

    from spl.token.instructions import get_associated_token_address
    spot_market = await get_spot_market_account(ch.program, spot_market_index)
    spot_mint = spot_market.mint
    print(spot_mint)

    ata = get_associated_token_address(wallet.public_key, spot_mint)
    ch.spot_market_atas[spot_market_index] = ata
    ch.usdc_ata = ata
    balance = await connection.get_token_account_balance(ata)
    print('current spot ata balance:', balance['result']['value']['uiAmount'])

    spot = await get_spot_market_account(ch.program, spot_market_index)
    total_shares = spot.insurance_fund.total_shares
    if_stake = await get_if_stake_account(ch.program, ch.authority, spot_market_index)
    n_shares = if_stake.if_shares

    print(f'{operation}ing {if_amount}$ spot...')
    if_amount = int(if_amount * QUOTE_PRECISION)

    if operation == 'add':
        resp = input('confirm adding stake: Y?')
        if resp != 'Y':
            print('confirmation failed exiting...')
            return

        rpc_resp = (
            await connection.get_account_info(get_insurance_fund_stake_public_key(
                ch.program_id, kp.public_key, spot_market_index
            ))
        )
        if rpc_resp["result"]["value"] is None:
            print('initializing stake account...')
            sig = await ch.initialize_insurance_fund_stake(spot_market_index)
            print(sig)

        print('adding stake ....')
        sig = await ch.add_insurance_fund_stake(spot_market_index, if_amount)
        print(sig)
    elif operation == 'remove':
        resp = input('confirm removing stake: Y?')
        if resp != 'Y':
            print('confirmation failed exiting...')
            return

        if if_amount == None: 
            vault_balance = (await connection.get_token_account_balance(
                get_insurance_fund_vault_public_key(
                    ch.program_id, spot_market_index
                )
            ))['result']['value']['uiAmount']
            spot_market = await get_spot_market_account(ch.program, spot_market_index)
            ifstake = await get_if_stake_account(
                ch.program, 
                ch.authority, 
                spot_market_index
            )
            total_amount = vault_balance * ifstake.if_shares / spot_market.insurance_fund.total_shares
            print(f'claimable amount: {total_amount}$')
            if_amount = int(total_amount * QUOTE_PRECISION)

        print('requesting to remove if stake...') 
        ix = await ch.request_remove_insurance_fund_stake(
            spot_market_index, if_amount
        )
        await view_logs(ix, connection)
        
        print('removing if stake...') 
        ix = await ch.remove_insurance_fund_stake(
            spot_market_index
        )
        await view_logs(ix, connection)

    elif operation == 'view': 
        conn = ch.program.provider.connection
        vault_pk = get_insurance_fund_vault_public_key(ch.program_id, spot_market_index)
        v_amount = int((await conn.get_token_account_balance(vault_pk))['result']['value']['amount'])
        balance = v_amount * n_shares / total_shares
        print(
            f'vault_amount: {v_amount/QUOTE_PRECISION:,.2f}$ \nn_shares: {n_shares} \ntotal_shares: {total_shares} \n>balance: {balance / QUOTE_PRECISION}'
        )
    else: 
        return

    if operation in ['add', 'remove']:
        ifstake = await get_if_stake_account(
            ch.program, 
            ch.authority, 
            spot_market_index
        )
        print('total if shares:', ifstake.if_shares)

    print('done! :)')

if __name__ == '__main__':
    import argparse
    import os 
    parser = argparse.ArgumentParser()
    parser.add_argument('--keypath', type=str, required=False, default=os.environ.get('ANCHOR_WALLET'))
    parser.add_argument('--env', type=str, default='devnet')
    parser.add_argument('--amount', type=float, required=False)
    parser.add_argument('--market', type=int, required=True)
    parser.add_argument('--operation', choices=['remove', 'add', 'view'], required=True)

    args = parser.parse_args()

    if args.operation == 'add':
        assert args.amount is not None, 'adding requires --amount'
    
    if args.operation == 'remove' and args.amount is None:
        print('removing full IF stake')

    if args.keypath is None:
        raise NotImplementedError("need to provide keypath or set ANCHOR_WALLET")

    match args.env:
        case 'devnet':
            url = 'https://api.devnet.solana.com'
        case 'mainnet':
            url = 'https://api.mainnet-beta.solana.com'
        case _:
            raise NotImplementedError('only devnet/mainnet env supported')

    import asyncio
    asyncio.run(main(
        args.keypath, 
        args.env, 
        url,
        args.market, 
        args.amount,
        args.operation,
    ))



